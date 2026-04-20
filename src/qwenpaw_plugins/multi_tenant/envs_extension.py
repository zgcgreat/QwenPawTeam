# -*- coding: utf-8 -*-
"""Envs store extensions: make load_envs / save_envs tenant-aware.

Strategy
--------
We monkey-patch the functions in ``qwenpaw.envs.store`` so that every
call site that uses ``load_envs()`` / ``save_envs()`` automatically
benefits from tenant routing without any upstream code changes.

The patched versions resolve the tenant ID from the async ContextVar
set by the auth middleware, then route ``envs.json`` to the tenant's
own secret directory instead of the global ``~/.qwenpaw.secret/``.

Tenant Directory Layout
-----------------------
::

    {SECRET_DIR}/                         ← ~/.qwenpaw.secret
    ├── envs.json                         ← root (single-user) envs store
    └── tenants/
        └── {tenant_id}/
            └── envs.json                 ← tenant-isolated envs store
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


def get_tenant_secret_dir(tenant_id: str) -> Path:
    """Return the secret directory for a specific tenant.

    Args:
        tenant_id: Composite tenant identifier.

    Returns:
        Path: ``{SECRET_DIR}/tenants/{tenant_id}/``
        e.g. ``~/.qwenpaw.secret/tenants/{tenant_id}/``
    """
    if not tenant_id or tenant_id == "default":
        return SECRET_DIR
    return SECRET_DIR / "tenants" / tenant_id


def get_tenant_envs_json_path(tenant_id: Optional[str] = None) -> Path:
    """Return envs.json path, tenant-aware.

    Resolution order:

    1. If *tenant_id* is explicitly passed and not ``"default"`` → tenant path.
    2. If the async context has a non-default tenant set → tenant path.
    3. Otherwise → root path (single-user mode).

    Args:
        tenant_id: Composite tenant ID.  If ``None``, checks context.

    Returns:
        Path to the appropriate ``envs.json``.
    """
    resolved = _resolve_tenant_id(tenant_id)
    if resolved and resolved != "default":
        return get_tenant_secret_dir(resolved) / "envs.json"
    return _original_get_envs_json_path()


def _resolve_tenant_id(
    tenant_id: Optional[str] = None,
) -> Optional[str]:
    """Resolve effective tenant ID from argument → context variable → None."""
    if tenant_id:
        return tenant_id
    try:
        from .tenant_context import get_current_tenant_id
        ctx_val = get_current_tenant_id()
        if ctx_val and ctx_val != "default":
            return ctx_val
    except Exception:
        pass
    return None


# ------------------------------------------------------------------
# Patched functions
# ------------------------------------------------------------------


def _patched_get_envs_json_path() -> Path:
    """Tenant-aware replacement for ``get_envs_json_path``."""
    return get_tenant_envs_json_path()


def _patched_load_envs(path=None) -> dict[str, str]:
    """Tenant-aware replacement for ``load_envs``.

    If *path* is not explicitly provided, resolves the tenant-specific
    envs.json.  Otherwise delegates to the original function unchanged.
    """
    if path is None:
        path = get_tenant_envs_json_path()
        # Run legacy migration for the tenant path as well
        from qwenpaw.envs.store import _migrate_legacy_envs_json
        _migrate_legacy_envs_json(path)
    return _original_load_envs(path=path)


def _patched_save_envs(envs: dict[str, str], path=None) -> None:
    """Tenant-aware replacement for ``save_envs``.

    If *path* is not explicitly provided, resolves the tenant-specific
    envs.json.  Otherwise delegates to the original function unchanged.
    """
    if path is None:
        path = get_tenant_envs_json_path()
        from qwenpaw.envs.store import _migrate_legacy_envs_json
        _migrate_legacy_envs_json(path)
    _original_save_envs(envs, path=path)


def _patched_set_env_var(key: str, value: str) -> dict[str, str]:
    """Tenant-aware replacement for ``set_env_var``.

    Uses the patched ``load_envs`` / ``save_envs`` which already
    route to the correct tenant path.
    """
    envs = _patched_load_envs()
    envs[key] = value
    _patched_save_envs(envs)
    return envs


def _patched_delete_env_var(key: str) -> dict[str, str]:
    """Tenant-aware replacement for ``delete_env_var``.

    Uses the patched ``load_envs`` / ``save_envs`` which already
    route to the correct tenant path.
    """
    envs = _patched_load_envs()
    envs.pop(key, None)
    _patched_save_envs(envs)
    return envs


# ------------------------------------------------------------------
# Patching
# ------------------------------------------------------------------


def patch_envs_store() -> None:
    """Monkey-patch ``qwenpaw.envs.store`` with tenant-aware variants.

    This is called during plugin activation so that all env var
    operations (including the ``/api/envs`` endpoints) automatically
    route to the correct tenant directory.

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

    logger.info("[multi-tenant/envs] Patched store, package, and router levels")


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

    logger.info("[multi-tenant/envs] Restored original envs store functions")
