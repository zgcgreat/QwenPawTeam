# -*- coding: utf-8 -*-
"""Multi-user plugin entry point for QwenPaw's new plugin system.

This module exports the ``plugin`` object that the PluginLoader discovers
and loads.  All multi-user functionality is registered via the PluginApi
interface — no modifications to upstream source code are needed.

Activation
----------
The plugin is automatically discovered and loaded by the PluginLoader
when it scans ``{WORKING_DIR}/plugins/`` (or the bundled ``plugins/bundle/``
directory).  No code changes in ``_app.py`` are required.

Architecture
------------
The plugin registers:

1. A **startup hook** that activates all multi-user extensions
   (config patching, auth patching, manager/provider wrapping, etc.)
   when the application starts.

2. An **HTTP router** that adds authentication endpoints under ``/api``.

3. A **shutdown hook** for cleanup.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from qwenpaw.plugins.api import PluginApi

logger = logging.getLogger(__name__)

# Ensure plugin directory is on sys.path so sibling modules can import
# each other via relative imports.
_plugin_dir = str(Path(__file__).resolve().parent)
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)


def _is_upstream_auth_route(route) -> bool:
    """Check if a route belongs to the upstream auth router.

    The upstream auth routes are registered under the ``auth`` tag and
    have paths starting with ``/api/auth/``.  We identify them by their
    path prefix so that we can remove them when the multi-user plugin
    provides its own enhanced auth routes.
    """
    path = getattr(route, "path", "")
    if not path.startswith("/api/auth"):
        return False
    # Don't remove routes that belong to our plugin (they have
    # a different route object identity).
    # We only want to remove the *upstream* auth routes, which
    # are defined in qwenpaw.app.routers.auth.
    route_module = getattr(getattr(route, "endpoint", None), "__module__", "")
    if route_module.startswith("qwenpaw.app.routers.auth"):
        return True
    return False


class MultiUserPlugin:
    """Multi-user support plugin for QwenPaw."""

    def register(self, api: PluginApi):
        """Register multi-user plugin hooks and routes.

        Called by the PluginLoader during plugin discovery.  All
        multi-user functionality is activated via startup/shutdown hooks
        so that it runs at the correct point in the application lifecycle.

        Parameters
        ----------
        api:
            The PluginApi instance provided by the plugin system.
        """
        logger.info("Registering multi-user plugin")

        # Register startup hook — runs during background startup, after
        # managers are created but before they serve requests.
        # Priority 10 = run early (lower number = higher priority).
        api.register_startup_hook(
            hook_name="multi_user_startup",
            callback=self._startup,
            priority=10,
        )

        # Register shutdown hook — runs during graceful shutdown.
        # Priority 200 = run late (cleanup last).
        api.register_shutdown_hook(
            hook_name="multi_user_shutdown",
            callback=self._shutdown,
            priority=200,
        )

        # Register auth HTTP routes.
        # NOTE: The upstream register_http_router() automatically prepends
        # "/api" to the prefix, so we only need to pass "/auth" here —
        # the resulting mount point will be /api/auth, matching the upstream
        # auth route layout.
        api.register_http_router(
            self._build_auth_router(),
            prefix="/auth",
            tags=["auth"],
        )

        # NOTE: AuthMiddleware patching is NOT done here. It must happen
        # at module-level in _app.py BEFORE app.add_middleware(AuthMiddleware)
        # because middleware registration occurs at import time, while this
        # register() method is called during _background_startup() — too late.

        logger.info("Multi-user plugin registered successfully")

    # ------------------------------------------------------------------
    # Startup hook
    # ------------------------------------------------------------------

    async def _startup(self):
        """Activate all multi-user extensions.

        This runs during the background startup phase, after the
        MultiAgentManager and ProviderManager have been created.
        """
        logger.info("=" * 60)
        logger.info("Activating QwenPaw multi-user plugin...")
        logger.info("=" * 60)

        # --- Step 0: Remove original auth routes to avoid conflicts ---
        # The upstream auth routes (/api/auth/login, /api/auth/status, etc.)
        # are registered at import time via routers/__init__.py.  Our plugin
        # registers its own enhanced auth routes at the same prefix.  FastAPI
        # matches routes in registration order, so the original routes take
        # precedence.  We remove the original ones here so that our enhanced
        # routes handle all /api/auth/* requests.
        try:
            from qwenpaw.app._app import app as _app
            # Verify that upstream auth routes were excluded by
            # routers/__init__.py (which checks for multi-user plugin dir).
            # If they weren't, log a warning.
            _has_upstream_auth = False
            for _r in _app.routes:
                if _is_upstream_auth_route(_r):
                    _has_upstream_auth = True
                    break
            if _has_upstream_auth:
                logger.warning(
                    "[multi-user] Upstream auth routes still present — "
                    "they may conflict with plugin routes. "
                    "Check routers/__init__.py for _skip_upstream_auth.",
                )
        except Exception as e:
            logger.warning(
                "[multi-user] Failed to check upstream auth routes: %s", e,
            )

        # --- Step 1: User context (always safe, no patching needed) ---
        from user_context import get_current_user_id  # noqa: F401
        logger.info("[multi-user] User context module loaded")

        # --- Step 2: Config extensions ---
        from config_extension import patch_config_utils
        patch_config_utils()
        logger.info("[multi-user] Config utils patched")

        # --- Step 2b: Envs store extensions ---
        from envs_extension import patch_envs_store
        patch_envs_store()
        logger.info("[multi-user] Envs store patched")

        # --- Step 2c: Agents router extension ---
        from agents_extension import patch_agents_router
        patch_agents_router()
        logger.info("[multi-user] Agents router patched")

        # --- Step 2d: Token usage manager extension ---
        from token_usage_extension import patch_token_usage_manager
        patch_token_usage_manager()
        logger.info("[multi-user] Token usage manager patched")

        # --- Step 2e: Console router extension ---
        from console_extension import patch_console_router
        patch_console_router()
        logger.info("[multi-user] Console router patched")

        # --- Step 2f: Backup router extension ---
        from backup_extension import patch_backup_router
        patch_backup_router()
        logger.info("[multi-user] Backup router patched")

        # --- Step 3: Auth extension was already patched in register() ---

        # --- Step 4: Wrap managers ---
        # We need to get the app instance and wrap the managers.
        from qwenpaw.app._app import app

        # Wrap MultiAgentManager
        from manager_extension import wrap_manager_for_user
        manager = app.state.multi_agent_manager
        wrapped = await wrap_manager_for_user(app, manager)
        app.state.multi_agent_manager = wrapped
        logger.info("[multi-user] MultiAgentManager wrapped")

        # Wrap ProviderManager
        from provider_extension import wrap_provider_for_user
        provider = app.state.provider_manager
        wrapped_provider = await wrap_provider_for_user(app, provider)
        app.state.provider_manager = wrapped_provider
        logger.info("[multi-user] ProviderManager wrapped")

        logger.info("Multi-user plugin activated successfully")
        logger.info("=" * 60)

    # ------------------------------------------------------------------
    # Shutdown hook
    # ------------------------------------------------------------------

    async def _shutdown(self):
        """Deactivate multi-user support during graceful shutdown."""
        logger.info("[multi-user] Shutting down multi-user plugin...")

        try:
            from token_usage_extension import unpatch_token_usage_manager
            unpatch_token_usage_manager()
            logger.info("[multi-user] Token usage manager unpatched")
        except Exception as e:
            logger.warning("[multi-user] Failed to unpatch token usage: %s", e)

        try:
            from console_extension import unpatch_console_router
            unpatch_console_router()
            logger.info("[multi-user] Console router unpatched")
        except Exception as e:
            logger.warning("[multi-user] Failed to unpatch console router: %s", e)

        try:
            from backup_extension import unpatch_backup_router
            unpatch_backup_router()
            logger.info("[multi-user] Backup router unpatched")
        except Exception as e:
            logger.warning("[multi-user] Failed to unpatch backup router: %s", e)

        logger.info("[multi-user] Multi-user plugin shutdown complete")

    # ------------------------------------------------------------------
    # Auth HTTP router
    # ------------------------------------------------------------------

    def _build_auth_router(self):
        """Build and return the auth APIRouter for multi-user endpoints."""
        from router_extension import get_auth_router
        return get_auth_router()


# The plugin object that the PluginLoader discovers.
plugin = MultiUserPlugin()
