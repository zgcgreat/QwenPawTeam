# -*- coding: utf-8 -*-
"""Backup extension: make backup operations user-aware.

Strategy
--------
Instead of wrapping route handlers and swapping module-level constants
(scheme used in v1, which failed because FastAPI ``include_router`` copies
route objects — modifying ``route.endpoint`` on the sub-router has no effect
on the app's routes), we replace the path constants themselves with
**dynamic-proxy objects** (``UserAwarePath``) that resolve to the correct
user directory on every attribute access.

This is the same approach used by ``console_extension.UserAwareLogPath``
and is inherently thread-safe — no ``asyncio.Lock`` or swap/restore needed.

User Backup Directory Layout
------------------------------
:::

    {BACKUP_DIR}/                           ← global backup root
    └── users/
        └── {user_id}/
            ├── {backup_id}.zip             ← per-user backup files
            └── _pre_restore_keys/          ← per-user master key backups

Patched Modules
---------------
Seven modules hold local ``from ...constant import X`` bindings:

1. ``qwenpaw.backup._utils.constants``  – BACKUP_DIR
2. ``qwenpaw.backup._ops.storage``      – BACKUP_DIR
3. ``qwenpaw.backup._ops.create``       – BACKUP_DIR
4. ``qwenpaw.backup._ops.create_helpers`` – WORKING_DIR, SECRET_DIR
5. ``qwenpaw.backup._ops.restore``      – WORKING_DIR, SECRET_DIR
6. ``qwenpaw.backup._ops.restore_helpers`` – BACKUP_DIR, SECRET_DIR, WORKING_DIR
7. ``qwenpaw.app.routers.backup``       – BACKUP_DIR
"""
from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path resolution helpers
# ---------------------------------------------------------------------------


def _resolve_user_id() -> Optional[str]:
    """Return the current request's user ID, or None for default."""
    try:
        from .user_context import get_current_user_id
        uid = get_current_user_id()
        if uid and uid != "default":
            return uid
    except Exception:
        pass
    return None


def _get_user_backup_dir(user_id: str) -> Path:
    """Return the backup directory for *user_id*."""
    from qwenpaw.constant import BACKUP_DIR
    d = BACKUP_DIR / "users" / user_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get_user_working_dir(user_id: str) -> Path:
    """Return the working directory for *user_id*."""
    from .auth_extension import get_user_working_dir
    return get_user_working_dir(user_id)


def _get_user_secret_dir(user_id: str) -> Path:
    """Return the secret directory for *user_id*."""
    from .auth_extension import get_user_secret_dir
    return get_user_secret_dir(user_id)


# ---------------------------------------------------------------------------
# UserAwarePath — dynamic proxy that resolves per-request
# ---------------------------------------------------------------------------


class UserAwarePath:
    """A Path-like object that dynamically resolves to user-specific paths.

    Every attribute access is delegated to the *currently-resolved* ``Path``
    object, which is computed by calling ``resolver(user_id)`` when a
    non-default user is active, or falling back to ``original`` otherwise.

    This makes the object safe for use in both sync and async contexts
    (including ``asyncio.to_thread`` workers) because it reads the
    ``ContextVar`` on every access rather than relying on any swap/restore
    dance.
    """

    __slots__ = ("_original", "_resolver")

    def __init__(self, original: Path, resolver: Callable[[str], Path]) -> None:
        object.__setattr__(self, "_original", original)
        object.__setattr__(self, "_resolver", resolver)

    def _current(self) -> Path:
        uid = _resolve_user_id()
        if uid:
            return self._resolver(uid)
        return self._original

    # --- Path-like operators ------------------------------------------------

    def __truediv__(self, other):
        return self._current() / other

    def __rtruediv__(self, other):
        return other / self._current()

    # --- Delegate everything else to the resolved Path ----------------------

    def __getattr__(self, name: str):
        return getattr(self._current(), name)

    def __str__(self) -> str:
        return str(self._current())

    def __fspath__(self) -> str:
        return str(self._current())

    def __repr__(self) -> str:
        return f"UserAwarePath({self._current()!r})"

    def __eq__(self, other):
        return self._current() == other

    def __hash__(self):
        return hash(self._current())

    def __bool__(self) -> bool:
        return bool(self._current())


# ---------------------------------------------------------------------------
# Patch descriptor: (module_path, [(attr_name, UserAwarePath), ...])
# ---------------------------------------------------------------------------

# Saved originals for unpatch
_originals: dict[tuple[str, str], object] = {}

# Lazy-created UserAwarePath instances (created once in patch)
_user_paths: dict[str, UserAwarePath] = {}


def _ensure_user_paths() -> None:
    """Create the UserAwarePath instances (idempotent)."""
    if _user_paths:
        return

    import qwenpaw.constant as const_module

    _user_paths["BACKUP_DIR"] = UserAwarePath(
        const_module.BACKUP_DIR, _get_user_backup_dir,
    )
    _user_paths["WORKING_DIR"] = UserAwarePath(
        const_module.WORKING_DIR, _get_user_working_dir,
    )
    _user_paths["SECRET_DIR"] = UserAwarePath(
        const_module.SECRET_DIR, _get_user_secret_dir,
    )


_PATCH_SPEC: list[tuple[str, list[tuple[str, str]]]] = [
    ("qwenpaw.backup._utils.constants", [("BACKUP_DIR", "BACKUP_DIR")]),
    ("qwenpaw.backup._ops.storage", [("BACKUP_DIR", "BACKUP_DIR")]),
    ("qwenpaw.backup._ops.create", [("BACKUP_DIR", "BACKUP_DIR")]),
    (
        "qwenpaw.backup._ops.create_helpers",
        [("WORKING_DIR", "WORKING_DIR"), ("SECRET_DIR", "SECRET_DIR")],
    ),
    (
        "qwenpaw.backup._ops.restore",
        [("WORKING_DIR", "WORKING_DIR"), ("SECRET_DIR", "SECRET_DIR")],
    ),
    (
        "qwenpaw.backup._ops.restore_helpers",
        [
            ("BACKUP_DIR", "BACKUP_DIR"),
            ("SECRET_DIR", "SECRET_DIR"),
            ("WORKING_DIR", "WORKING_DIR"),
        ],
    ),
    ("qwenpaw.app.routers.backup", [("BACKUP_DIR", "BACKUP_DIR")]),
]


# ---------------------------------------------------------------------------
# Patch / unpatch
# ---------------------------------------------------------------------------


def patch_backup_router() -> None:
    """Replace path constants in backup modules with UserAwarePath proxies.

    Each module-level constant (e.g. ``BACKUP_DIR``) is replaced with a
    ``UserAwarePath`` instance that dynamically resolves to the current
    user's directory on every access.  No route wrapping or constant
    swapping is needed.
    """
    _ensure_user_paths()

    count = 0
    for module_path, attrs in _PATCH_SPEC:
        try:
            mod = importlib.import_module(module_path)
        except ImportError:
            logger.debug("[multi-user/backup] Skip %s (import error)", module_path)
            continue
        for local_attr, path_key in attrs:
            original = getattr(mod, local_attr, None)
            if original is None:
                continue
            # Save original for unpatch
            _originals[(module_path, local_attr)] = original
            # Replace with UserAwarePath proxy
            setattr(mod, local_attr, _user_paths[path_key])
            count += 1

    logger.info(
        "[multi-user/backup] Replaced %d path constant(s) with "
        "UserAwarePath proxies (user isolation enabled)",
        count,
    )


def unpatch_backup_router() -> None:
    """Restore original path constants in backup modules."""
    for (module_path, local_attr), original in _originals.items():
        try:
            mod = importlib.import_module(module_path)
            setattr(mod, local_attr, original)
        except (ImportError, AttributeError):
            pass
    _originals.clear()
    _user_paths.clear()
    logger.info("[multi-user/backup] Restored original path constants")
