# -*- coding: utf-8 -*-
"""Backup extension: make backup operations tenant-aware.

Strategy
--------
Instead of wrapping route handlers and swapping module-level constants
(scheme used in v1, which failed because FastAPI ``include_router`` copies
route objects — modifying ``route.endpoint`` on the sub-router has no effect
on the app's routes), we replace the path constants themselves with
**dynamic-proxy objects** (``TenantAwarePath``) that resolve to the correct
tenant directory on every attribute access.

This is the same approach used by ``console_extension.TenantAwareLogPath``
and is inherently thread-safe — no ``asyncio.Lock`` or swap/restore needed.

Tenant Backup Directory Layout
------------------------------
::::

    {BACKUP_DIR}/                           ← global backup root
    └── tenants/
        └── {tenant_id}/
            ├── {backup_id}.zip             ← per-tenant backup files
            └── _pre_restore_keys/          ← per-tenant master key backups

Patched Modules
---------------
Six modules hold local ``from ...constant import X`` bindings:

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


def _resolve_tenant_id() -> Optional[str]:
    """Return the current request's tenant ID, or None for default."""
    try:
        from .tenant_context import get_current_tenant_id
        tid = get_current_tenant_id()
        if tid and tid != "default":
            return tid
    except Exception:
        pass
    return None


def _get_tenant_backup_dir(tenant_id: str) -> Path:
    """Return the backup directory for *tenant_id*."""
    from qwenpaw.constant import BACKUP_DIR
    d = BACKUP_DIR / "tenants" / tenant_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get_tenant_working_dir(tenant_id: str) -> Path:
    """Return the working directory for *tenant_id*."""
    from .auth_extension import get_tenant_working_dir
    return get_tenant_working_dir(tenant_id)


def _get_tenant_secret_dir(tenant_id: str) -> Path:
    """Return the secret directory for *tenant_id*."""
    from .auth_extension import get_tenant_secret_dir
    return get_tenant_secret_dir(tenant_id)


# ---------------------------------------------------------------------------
# TenantAwarePath — dynamic proxy that resolves per-request
# ---------------------------------------------------------------------------


class TenantAwarePath:
    """A Path-like object that dynamically resolves to tenant-specific paths.

    Every attribute access is delegated to the *currently-resolved* ``Path``
    object, which is computed by calling ``resolver(tenant_id)`` when a
    non-default tenant is active, or falling back to ``original`` otherwise.

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
        tid = _resolve_tenant_id()
        if tid:
            return self._resolver(tid)
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
        return f"TenantAwarePath({self._current()!r})"

    def __eq__(self, other):
        return self._current() == other

    def __hash__(self):
        return hash(self._current())

    def __bool__(self) -> bool:
        return bool(self._current())


# ---------------------------------------------------------------------------
# Patch descriptor: (module_path, [(attr_name, TenantAwarePath), ...])
# ---------------------------------------------------------------------------

# Saved originals for unpatch
_originals: dict[tuple[str, str], object] = {}

# Lazy-created TenantAwarePath instances (created once in patch)
_tenant_paths: dict[str, TenantAwarePath] = {}


def _ensure_tenant_paths() -> None:
    """Create the TenantAwarePath instances (idempotent)."""
    if _tenant_paths:
        return

    import qwenpaw.constant as const_module

    _tenant_paths["BACKUP_DIR"] = TenantAwarePath(
        const_module.BACKUP_DIR, _get_tenant_backup_dir,
    )
    _tenant_paths["WORKING_DIR"] = TenantAwarePath(
        const_module.WORKING_DIR, _get_tenant_working_dir,
    )
    _tenant_paths["SECRET_DIR"] = TenantAwarePath(
        const_module.SECRET_DIR, _get_tenant_secret_dir,
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
    """Replace path constants in backup modules with TenantAwarePath proxies.

    Each module-level constant (e.g. ``BACKUP_DIR``) is replaced with a
    ``TenantAwarePath`` instance that dynamically resolves to the current
    tenant's directory on every access.  No route wrapping or constant
    swapping is needed.
    """
    _ensure_tenant_paths()

    count = 0
    for module_path, attrs in _PATCH_SPEC:
        try:
            mod = importlib.import_module(module_path)
        except ImportError:
            logger.debug("[multi-tenant/backup] Skip %s (import error)", module_path)
            continue
        for local_attr, path_key in attrs:
            original = getattr(mod, local_attr, None)
            if original is None:
                continue
            # Save original for unpatch
            _originals[(module_path, local_attr)] = original
            # Replace with TenantAwarePath proxy
            setattr(mod, local_attr, _tenant_paths[path_key])
            count += 1

    logger.info(
        "[multi-tenant/backup] Replaced %d path constant(s) with "
        "TenantAwarePath proxies (tenant isolation enabled)",
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
    _tenant_paths.clear()
    logger.info("[multi-tenant/backup] Restored original path constants")
