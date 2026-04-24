# -*- coding: utf-8 -*-
"""Agents extension: makes workspace base directory user-aware.

Strategy
--------
Instead of monkey-patching a custom helper function in the upstream
``agents.py`` (which creates merge conflicts every time the upstream
is updated), we replace the module-level ``WORKING_DIR`` binding inside
``qwenpaw.app.routers.agents`` with a **dynamic-proxy object**
(``UserAwarePath``).

When a non-default user is active, ``UserAwarePath.__str__()``
returns the user's working directory; otherwise it falls back to the
global ``WORKING_DIR``.  This means the upstream code

    f"{WORKING_DIR}/workspaces/{new_id}"

automatically resolves to the correct per-user path — zero changes
required in upstream code.

This is the same approach used by ``backup_extension`` and
``console_extension``, and is inherently thread-safe because it reads
the ``ContextVar`` on every access.
"""
from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path resolution helpers (shared with backup_extension)
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


def _get_user_working_dir(user_id: str) -> Path:
    """Return the working directory for *user_id*."""
    from .auth_extension import get_user_working_dir
    return get_user_working_dir(user_id)


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
# Patch / unpatch
# ---------------------------------------------------------------------------

# Saved originals for unpatch
_originals: dict[tuple[str, str], object] = {}


def patch_agents_router() -> None:
    """Replace the ``WORKING_DIR`` constant in the agents router module
    with a ``UserAwarePath`` proxy.

    When ``agents.py`` evaluates ``f"{WORKING_DIR}/workspaces/..."``,
    the proxy's ``__str__()`` returns the current user's working
    directory — achieving per-user workspace isolation without any
    changes to upstream code.
    """
    import qwenpaw.constant as const_module
    import qwenpaw.app.routers.agents as agents_module

    user_aware_working_dir = UserAwarePath(
        const_module.WORKING_DIR,
        _get_user_working_dir,
    )

    # Save original for unpatch
    _originals[("qwenpaw.app.routers.agents", "WORKING_DIR")] = (
        agents_module.WORKING_DIR
    )

    # Replace with UserAwarePath proxy
    setattr(agents_module, "WORKING_DIR", user_aware_working_dir)

    logger.info(
        "[multi-user/agents] Replaced WORKING_DIR in agents router "
        "with UserAwarePath proxy (user isolation enabled)",
    )


def unpatch_agents_router() -> None:
    """Restore original ``WORKING_DIR`` in the agents router module."""
    import qwenpaw.app.routers.agents as agents_module

    key = ("qwenpaw.app.routers.agents", "WORKING_DIR")
    original = _originals.pop(key, None)
    if original is not None:
        setattr(agents_module, "WORKING_DIR", original)
        logger.info("[multi-user/agents] Restored original WORKING_DIR in agents router")
