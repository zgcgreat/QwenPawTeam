# -*- coding: utf-8 -*-
"""Multi-tenant plugin for QwenPaw.

This plugin adds multi-tenant (multi-user) support to QwenPaw without
modifying any source files under ``qwenpaw/``.  It uses a combination of
wrapper classes and selective monkey-patching to extend the existing
single-user system.

Activation
----------
Set the environment variable ``QWENPAW_MULTI_TENANT_ENABLED=true`` before
starting the application.  The activation hook in
``qwenpaw/app/_app.py`` will call :func:`activate_multi_tenant`.

Architecture
-------------
```
qwenpaw/                    ← upstream code (NEVER modified)
│
└── qwenpaw_plugins/
    └── multi_tenant/       ← this plugin
        ├── tenant_context.py     ContextVar-based tenant propagation
        ├── token_parser.py       Pluggable business-system token parser
        ├── auth_extension.py     Multi-user auth + middleware
        ├── router_extension.py   Additional API endpoints
        ├── manager_extension.py  Tenant-isolated MultiAgentManager
        ├── provider_extension.py Tenant-aware ProviderManager overlays
        ├── config_extension.py   Tenant-aware config loading/saving
        ├── migration_extension.py Tenant workspace initialization
        └── constants.py          Plugin-level constants
```
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import FastAPI

logger = logging.getLogger(__name__)

#: Tracks whether the plugin is currently active.
_active: bool = False


def is_active() -> bool:
    """Return ``True`` if the multi-tenant plugin is currently active."""
    return _active


def activate_multi_tenant(app: FastAPI) -> None:
    """Activate multi-tenant support.

    This function should be called once during application startup,
    **before** any request handlers are mounted.  It:

    1. Installs the tenant-context propagation layer.
    2. Patches ``qwenpaw.config.utils`` with tenant-aware variants.
    3. Replaces the auth module with the multi-tenant extension.
    4. Registers additional auth API endpoints.
    5. Wraps ``MultiAgentManager`` and ``ProviderManager``.
    6. Adds the ``AuthMiddleware`` to the middleware stack.

    Parameters
    ----------
    app:
        The FastAPI application instance.

    Raises
    ------
    RuntimeError
        If called more than once or if the upstream modules cannot be
        imported.
    """
    global _active
    if _active:
        logger.warning("Multi-tenant plugin is already active, skipping.")
        return

    logger.info("=" * 60)
    logger.info("Activating QwenPaw multi-tenant plugin...")
    logger.info("=" * 60)

    # --- Step 1: Tenant context (always safe, no patching needed) ---
    from . import tenant_context  # noqa: F401  # ensure module is loaded

    # --- Step 2: Config extensions ---
    from .config_extension import patch_config_utils
    patch_config_utils()
    logger.info("[multi-tenant] Config utils patched")

    # --- Step 2b: Envs store extensions ---
    from .envs_extension import patch_envs_store
    patch_envs_store()
    logger.info("[multi-tenant] Envs store patched")

    # --- Step 2c: Agents router extension ---
    from .agents_extension import patch_agents_router
    patch_agents_router()
    logger.info("[multi-tenant] Agents router patched")

    # --- Step 2d: Token usage manager extension ---
    from .token_usage_extension import patch_token_usage_manager
    patch_token_usage_manager()
    logger.info("[multi-tenant] Token usage manager patched")

    # --- Step 2e: Console router extension (backend-logs tenant isolation) ---
    from .console_extension import patch_console_router
    patch_console_router()
    logger.info("[multi-tenant] Console router patched")

    # --- Step 3: Auth extension (replaces auth module) ---
    from .auth_extension import patch_auth_module
    patch_auth_module()
    logger.info("[multi-tenant] Auth module patched")

    # --- Step 4: Register auth routes (under /api prefix to match upstream) ---
    from .router_extension import get_auth_router
    auth_router = get_auth_router()
    app.include_router(auth_router, prefix="/api")
    logger.info("[multi-tenant] Auth routes registered")

    # --- Step 5: AuthMiddleware ---
    # AuthMiddleware is added in _app.py via `app.add_middleware(AuthMiddleware)`.
    # Since patch_auth_module() already replaced the upstream AuthMiddleware class,
    # the _app.py line automatically uses our multi-tenant version — no need to
    # add it again here.

    # --- Step 6: Manager wrappers via lifespan hooks ---
    # The MultiAgentManager and ProviderManager are created inside the
    # lifespan startup in ``_app.py``, *after* this function runs.
    # We register lifespan hook callbacks so that ``_app.py`` calls us
    # at the right time to wrap these managers with tenant-aware versions.
    from qwenpaw_plugins import register_lifespan_hook
    from .manager_extension import wrap_manager_for_tenant
    from .provider_extension import wrap_provider_for_tenant

    register_lifespan_hook("post_manager_init", wrap_manager_for_tenant)
    register_lifespan_hook("post_provider_init", wrap_provider_for_tenant)
    logger.info("[multi-tenant] Lifespan hooks registered")

    _active = True
    logger.info("Multi-tenant plugin activated successfully ✅")
    logger.info("=" * 60)


def deactivate_multi_tenant() -> None:
    """Deactivate multi-tenant support and undo all patches.

    .. warning::
        This is intended primarily for testing.  Deactivating mid-runtime
        in production is not supported and may leave the system in an
        inconsistent state.
    """
    global _active
    if not _active:
        return

    # TODO: implement per-module deactivation
    from .token_usage_extension import unpatch_token_usage_manager
    unpatch_token_usage_manager()
    logger.info("[multi-tenant] Token usage manager unpatched")
    from .console_extension import unpatch_console_router
    unpatch_console_router()
    logger.info("[multi-tenant] Console router unpatched")
    _active = False
    logger.info("Multi-tenant plugin deactivated")


# ---------------------------------------------------------------------------
# Convenience: check env var at import time
# ---------------------------------------------------------------------------

_ENV_FLAG = os.environ.get("QWENPAW_MULTI_TENANT_ENABLED", "true").lower()
if _ENV_FLAG in ("true", "1", "yes"):
    logger.info(
        "QWENPAW_MULTI_TENANT_ENABLED=%s — "
        "call activate_multi_tenant(app) during startup",
        _ENV_FLAG,
    )
