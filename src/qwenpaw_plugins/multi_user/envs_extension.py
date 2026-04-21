# -*- coding: utf-8 -*-
"""Envs store extensions: make load_envs / save_envs user-aware.

Strategy
--------
We monkey-patch the functions in ``qwenpaw.envs.store`` so that every
call site that uses ``load_envs()`` / ``save_envs()`` automatically
benefits from user routing without any upstream code changes.

The patched versions resolve the user ID from the async ContextVar
set by the auth middleware, then route ``envs.json`` to the user's
own secret directory instead of the global ``~/.qwenpaw.secret/``.

User Directory Layout
--------------------
:::

    {SECRET_DIR}/                         ← ~/.qwenpaw.secret
    ├── envs.json                         ← root (single-user) envs store
    └── users/
        └── {user_id}/
            └── envs.json                 ← user-isolated envs store
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from qwenpaw.constant import SECRET_DIR, WORKING_DIR

logger = logging.getLogger(__name__)

#: Keep references to the original functions so we can delegate.
_original_get_envs_json_path = None
_original_load_envs = None
_original_save_envs = None
_original_set_env_var = None
_original_delete_env_var = None


def get_user_secret_dir(user_id: str) -> Path:
    """Return the secret directory for a specific user.

    Args:
        user_id: Composite user identifier.

    Returns:
        Path: ``{SECRET_DIR}/users/{user_id}/``
        e.g. ``~/.qwenpaw.secret/users/{user_id}/``
    """
    if not user_id or user_id == "default":
        return SECRET_DIR
    return SECRET_DIR / "users" / user_id


def get_user_envs_json_path(user_id: Optional[str] = None) -> Path:
    """Return envs.json path, user-aware.

    Resolution order:

    1. If *user_id* is explicitly passed and not ``"default"`` → user path.
    2. If the async context has a non-default user set → user path.
    3. Otherwise → root path (single-user mode).

    Args:
        user_id: Composite user ID.  If ``None``, checks context.

    Returns:
        Path to the appropriate ``envs.json``.
    """
    resolved = _resolve_user_id(user_id)
    if resolved and resolved != "default":
        return get_user_secret_dir(resolved) / "envs.json"
    return _original_get_envs_json_path()


def _resolve_user_id(
    user_id: Optional[str] = None,
) -> Optional[str]:
    """Resolve effective user ID from argument → context variable → None."""
    if user_id:
        return user_id
    try:
        from .user_context import get_current_user_id
        ctx_val = get_current_user_id()
        if ctx_val and ctx_val != "default":
            return ctx_val
    except Exception:
        pass
    return None


# ------------------------------------------------------------------
# Patched functions
# ------------------------------------------------------------------


def _patched_get_envs_json_path() -> Path:
    """User-aware replacement for ``get_envs_json_path``."""
    return get_user_envs_json_path()


def _patched_load_envs(path=None) -> dict[str, str]:
    """User-aware replacement for ``load_envs``.

    If *path* is not explicitly provided, resolves the user-specific
    envs.json.  Otherwise delegates to the original function unchanged.
    """
    if path is None:
        path = get_user_envs_json_path()
        # Run legacy migration for the user path as well
        from qwenpaw.envs.store import _migrate_legacy_envs_json
        _migrate_legacy_envs_json(path)
    return _original_load_envs(path=path)


def _patched_save_envs(envs: dict[str, str], path=None) -> None:
    """User-aware replacement for ``save_envs``.

    If *path* is not explicitly provided, resolves the user-specific
    envs.json.  Otherwise delegates to the original function unchanged.
    """
    if path is None:
        path = get_user_envs_json_path()
        from qwenpaw.envs.store import _migrate_legacy_envs_json
        _migrate_legacy_envs_json(path)
    _original_save_envs(envs, path=path)


def _patched_set_env_var(key: str, value: str) -> dict[str, str]:
    """User-aware replacement for ``set_env_var``.

    Uses the patched ``load_envs`` / ``save_envs`` which already
    route to the correct user path.
    """
    envs = _patched_load_envs()
    envs[key] = value
    _patched_save_envs(envs)
    return envs


def _patched_delete_env_var(key: str) -> dict[str, str]:
    """User-aware replacement for ``delete_env_var``.

    Uses the patched ``load_envs`` / ``save_envs`` which already
    route to the correct user path.
    """
    envs = _patched_load_envs()
    envs.pop(key, None)
    _patched_save_envs(envs)
    return envs


# ------------------------------------------------------------------
# Patching
# ------------------------------------------------------------------


def patch_envs_store() -> None:
    """Monkey-patch ``qwenpaw.envs.store`` with user-aware variants.

    This is called during plugin activation so that all env var
    operations (including the ``/api/envs`` endpoints) automatically
    route to the correct user directory.

    We patch **three** levels to ensure no stale local bindings remain:

    1. ``qwenpaw.envs.store`` — the底层 implementation module.
    2. ``qwenpaw.envs`` — the package-level re-exports.
    3. ``qwenpaw.app.routers.envs`` — the router module which imports
       ``load_envs``, ``save_envs``, ``delete_env_var`` as local names
       at module load time.  Without patching this level, the router
       would still call the original (global-path) functions.
    """
    global _original_get_envs_json_path, _original_load_envs, _original_save_envs
    global _original_set_env_var, _original_delete_env_var

    import qwenpaw.envs.store as store_module

    # Save originals (only once — idempotent)
    if _original_get_envs_json_path is None:
        _original_get_envs_json_path = store_module.get_envs_json_path
        _original_load_envs = store_module.load_envs
        _original_save_envs = store_module.save_envs
        _original_set_env_var = store_module.set_env_var
        _original_delete_env_var = store_module.delete_env_var

    # Level 1: Patch the底层 store module
    store_module.get_envs_json_path = _patched_get_envs_json_path
    store_module.load_envs = _patched_load_envs
    store_module.save_envs = _patched_save_envs
    store_module.set_env_var = _patched_set_env_var
    store_module.delete_env_var = _patched_delete_env_var

    # Level 2: Patch the package-level re-exports (qwenpaw.envs)
    try:
        import qwenpaw.envs as envs_pkg
        envs_pkg.load_envs = _patched_load_envs
        envs_pkg.save_envs = _patched_save_envs
        envs_pkg.set_env_var = _patched_set_env_var
        envs_pkg.delete_env_var = _patched_delete_env_var
    except ImportError:
        pass

    # Level 3: Patch the router module's local bindings
    # The router does ``from ...envs import load_envs, save_envs, delete_env_var``
    # which creates local references that are NOT updated by patching the
    # package.  We must replace them directly on the router module object.
    try:
        import qwenpaw.app.routers.envs as envs_router
        envs_router.load_envs = _patched_load_envs
        envs_router.save_envs = _patched_save_envs
        envs_router.delete_env_var = _patched_delete_env_var
    except ImportError:
        pass

    logger.info("[multi-user/envs] Patched store, package, and router levels")


def unpatch_envs_store() -> None:
    """Restore original functions (for testing / deactivation)."""
    global _original_get_envs_json_path, _original_load_envs, _original_save_envs
    global _original_set_env_var, _original_delete_env_var

    if _original_get_envs_json_path is None:
        return

    import qwenpaw.envs.store as store_module

    store_module.get_envs_json_path = _original_get_envs_json_path
    store_module.load_envs = _original_load_envs
    store_module.save_envs = _original_save_envs
    store_module.set_env_var = _original_set_env_var
    store_module.delete_env_var = _original_delete_env_var

    try:
        import qwenpaw.envs as envs_pkg
        envs_pkg.load_envs = _original_load_envs
        envs_pkg.save_envs = _original_save_envs
        envs_pkg.set_env_var = _original_set_env_var
        envs_pkg.delete_env_var = _original_delete_env_var
    except ImportError:
        pass

    try:
        import qwenpaw.app.routers.envs as envs_router
        envs_router.load_envs = _original_load_envs
        envs_router.save_envs = _original_save_envs
        envs_router.delete_env_var = _original_delete_env_var
    except ImportError:
        pass

    logger.info("[multi-user/envs] Restored original envs store functions")
