# -*- coding: utf-8 -*-
"""Multi-user plugin for QwenPaw.

This plugin adds multi-user support to QwenPaw without
modifying any source files under ``qwenpaw/``.  It uses a combination of
wrapper classes and selective monkey-patching to extend the existing
single-user system.

Activation
----------
Set the environment variable ``QWENPAW_MULTI_USER_ENABLED=true`` before
starting the application.  The activation hook in
``qwenpaw/app/_app.py`` will call :func:`activate_multi_user`.

Architecture
------------
```
qwenpaw/                    ← upstream code (NEVER modified)
│
└── qwenpaw_plugins/
    └── multi_user/       ← this plugin
        ├── user_context.py     ContextVar-based user propagation
        ├── token_parser.py       Pluggable business-system token parser
        ├── auth_extension.py     Multi-user auth + middleware
        ├── router_extension.py   Additional API endpoints
        ├── manager_extension.py  User-isolated MultiAgentManager
        ├── provider_extension.py User-aware ProviderManager overlays
        ├── config_extension.py   User-aware config loading/saving
        ├── token_usage_extension.py  Per-agent token usage isolation
        ├── console_extension.py Console log path user isolation
        ├── backup_extension.py  User-isolated backup/restore
        ├── migration_extension.py User workspace initialization
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
    """Return ``True`` if the multi-user plugin is currently active."""
    return _active


def activate_multi_user(app: FastAPI) -> None:
    """Activate multi-user support.

    This function should be called once during application startup,
    **before** any request handlers are mounted.  It:

    1. Installs the user-context propagation layer.
    2. Patches ``qwenpaw.config.utils`` with user-aware variants.
    3. Replaces the auth module with the multi-user extension.
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
        logger.warning("Multi-user plugin is already active, skipping.")
        return

    logger.info("=" * 60)
    logger.info("Activating QwenPaw multi-user plugin...")
    logger.info("=" * 60)

    # --- Step 1: User context (always safe, no patching needed) ---
    from . import user_context  # noqa: F401  # ensure module is loaded

    # --- Step 2: Config extensions ---
    from .config_extension import patch_config_utils
    patch_config_utils()
    logger.info("[multi-user] Config utils patched")

    # --- Step 2b: Envs store extensions ---
    from .envs_extension import patch_envs_store
    patch_envs_store()
    logger.info("[multi-user] Envs store patched")

    # --- Step 2c: Agents router extension ---
    from .agents_extension import patch_agents_router
    patch_agents_router()
    logger.info("[multi-user] Agents router patched")

    # --- Step 2d: Token usage manager extension ---
    from .token_usage_extension import patch_token_usage_manager
    patch_token_usage_manager()
    logger.info("[multi-user] Token usage manager patched")

    # --- Step 2e: Console router extension (backend-logs user isolation) ---
    from .console_extension import patch_console_router
    patch_console_router()
    logger.info("[multi-user] Console router patched")

    # --- Step 2f: Backup router extension (user-isolated backups) ---
    from .backup_extension import patch_backup_router
    patch_backup_router()
    logger.info("[multi-user] Backup router patched")

    # --- Step 3: Auth extension (replaces auth module) ---
    from .auth_extension import patch_auth_module
    patch_auth_module()
    logger.info("[multi-user] Auth module patched")

    # --- Step 4: Register auth routes (under /api prefix to match upstream) ---
    from .router_extension import get_auth_router
    auth_router = get_auth_router()
    app.include_router(auth_router, prefix="/api")
    logger.info("[multi-user] Auth routes registered")

    # --- Step 5: AuthMiddleware ---
    # AuthMiddleware is added in _app.py via `app.add_middleware(AuthMiddleware)`.
    # Since patch_auth_module() already replaced the upstream AuthMiddleware class,
    # the _app.py line automatically uses our multi-user version — no need to
    # add it again here.

    # --- Step 6: Manager wrappers via lifespan hooks ---
    # The MultiAgentManager and ProviderManager are created inside the
    # lifespan startup in ``_app.py``, *after* this function runs.
    # We register lifespan hook callbacks so that ``_app.py`` calls us
    # at the right time to wrap these managers with user-aware versions.
    from qwenpaw_plugins import register_lifespan_hook
    from .manager_extension import wrap_manager_for_user
    from .provider_extension import wrap_provider_for_user

    register_lifespan_hook("post_manager_init", wrap_manager_for_user)
    register_lifespan_hook("post_provider_init", wrap_provider_for_user)
    logger.info("[multi-user] Lifespan hooks registered")

    _active = True
    logger.info("Multi-user plugin activated successfully")
    logger.info("=" * 60)


def deactivate_multi_user() -> None:
    """Deactivate multi-user support and undo all patches.

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
    logger.info("[multi-user] Token usage manager unpatched")
    from .console_extension import unpatch_console_router
    unpatch_console_router()
    logger.info("[multi-user] Console router unpatched")
    from .backup_extension import unpatch_backup_router
    unpatch_backup_router()
    logger.info("[multi-user] Backup router unpatched")
    _active = False
    logger.info("Multi-user plugin deactivated")


# ---------------------------------------------------------------------------
# Convenience: check env var at import time
# ---------------------------------------------------------------------------

_ENV_FLAG = os.environ.get("QWENPAW_MULTI_USER_ENABLED", "true").lower()
if _ENV_FLAG in ("true", "1", "yes"):
    logger.info(
        "QWENPAW_MULTI_USER_ENABLED=%s — "
        "call activate_multi_user(app) during startup",
        _ENV_FLAG,
    )
