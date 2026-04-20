# -*- coding: utf-8 -*-
"""Manager extension: wraps MultiAgentManager with per-tenant isolation.

Strategy
--------
Rather than replacing the original ``MultiAgentManager`` class, we create
a wrapper that adds a two-level dict ``(tenant_id → {agent_id → Workspace})``
on top of the original single-level dict.  The wrapper intercepts all public
methods and routes them to the correct tenant's sub-dict.

The global singleton is patched after it is first created.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional, Set

logger = logging.getLogger(__name__)

#: Reference to the original class (set during patching)
_OriginalManager = None

#: The wrapped singleton instance (replaces the global one)
_wrapped_instance = None


class TenantAwareMultiAgentManager:
    """Wraps the original MultiAgentManager with per-tenant isolation.

    Each tenant gets their own dict of Workspace instances, so two tenants
    can have agents with the same name without interfering.

    **Fallback logic**: Each tenant first attempts to lazy-load its own
    Workspace from its own config (which points to the tenant's isolated
    workspace_dir).  Only when the tenant's config does NOT define the
    requested agent_id do we fall back to the "default" tenant's cached
    Workspace.  This prevents cross-tenant data leakage (e.g. files,
    sessions) while still allowing shared access when appropriate.
    """

    def __init__(self, real_manager):
        self._real = real_manager  # Original MultiAgentManager instance
        self._tenants: Dict[str, Dict] = {}
        self._lock = asyncio.Lock()
        self._cleanup_tasks: Set[asyncio.Task] = set()
        logger.debug("TenantAwareMultiAgentManager initialized")

    # -- Tenant helpers --

    def _tenant_agents(self, tenant_id: str) -> Dict:
        if tenant_id not in self._tenants:
            self._tenants[tenant_id] = {}
        return self._tenants[tenant_id]

    def _current_tenant_id(self) -> str:
        try:
            from .tenant_context import get_current_tenant_id
            return get_current_tenant_id()
        except Exception:
            return "default"

    # -- Backward compat: `.agents` returns current tenant's agents --

    @property
    def agents(self) -> Dict:
        return self._tenant_agents(self._current_tenant_id())

    # -- Core operations --

    async def get_agent(self, agent_id: str, tenant_id: Optional[str] = None):
        """Get agent by ID, isolated per tenant.

        Resolution order:
        1. Current tenant's cache
        2. Lazy-load from tenant's own config (creates tenant-specific workspace)
        3. "default" tenant's cache (shared agents fallback — only if tenant
           has no config for the requested agent_id)
        """
        effective = tenant_id or self._current_tenant_id()
        tenant_agents = self._tenant_agents(effective)

        logger.info(
            "[multi-tenant/manager] get_agent: agent_id=%s, tenant_id_arg=%s, effective=%s",
            agent_id, tenant_id, effective,
        )

        # Step 1: Check current tenant's cache
        if agent_id in tenant_agents:
            return tenant_agents[agent_id]

        # Step 2: Lazy-load from tenant's own config (with lock)
        # This is the primary path — each tenant should get its own Workspace
        # with its own workspace_dir, even if the agent_id is the same as
        # the default tenant's.
        async with self._lock:
            # Double-check after acquiring lock
            if agent_id in tenant_agents:
                return tenant_agents[agent_id]

            logger.info(
                "[multi-tenant/manager] get_agent lazy-load: "
                "agent_id=%s, tenant=%s, existing_tenants=%s",
                agent_id, effective, list(self._tenants.keys()),
            )

            from qwenpaw.config.utils import load_config
            from .config_extension import ensure_tenant_config_exists

            # Last-resort: ensure tenant config exists (covers legacy
            # registrations that pre-date config.json auto-generation).
            if effective != "default":
                ensure_tenant_config_exists(effective)

            config = load_config(tenant_id=effective)

            if agent_id in config.agents.profiles:
                agent_ref = config.agents.profiles[agent_id]

                from qwenpaw.app.workspace import Workspace

                instance = Workspace(
                    agent_id=agent_id,
                    workspace_dir=agent_ref.workspace_dir,
                )

                await instance.start()
                instance.set_manager(self)
                tenant_agents[agent_id] = instance
                logger.info(
                    "[multi-tenant/manager] Created agent '%s' for tenant '%s': "
                    "started=%s, channel_manager=%s, workspace_dir=%s",
                    agent_id, effective, instance._started,
                    instance.channel_manager, instance.workspace_dir,
                )
                return instance

            # Step 3: Fallback to "default" tenant's cache only if the
            # current tenant has NO config for this agent_id.  This avoids
            # cross-tenant data leakage while still allowing shared access
            # to the default agent when a tenant has no config of its own.
            if effective != "default":
                default_agents = self._tenants.get("default", {})
                if agent_id in default_agents:
                    logger.info(
                        "[multi-tenant/manager] get_agent fallback: "
                        "agent_id=%s not in tenant '%s' config, using shared default",
                        agent_id, effective,
                    )
                    return default_agents[agent_id]

            raise ValueError(
                f"Agent '{agent_id}' not found in config for "
                f"tenant '{effective}'. Available: "
                f"{list(config.agents.profiles.keys())}"
            )

    async def stop_agent(self, agent_id: str, tenant_id: Optional[str] = None) -> bool:
        """Stop a specific agent instance."""
        effective = tenant_id or self._current_tenant_id()
        tenant_agents = self._tenant_agents(effective)

        if agent_id not in tenant_agents:
            return False

        instance = tenant_agents[agent_id]
        await instance.stop()
        del tenant_agents[agent_id]
        return True

    async def stop_all(self):
        """Stop all agents across all tenants."""
        total = sum(len(v) for v in self._tenants.values())
        logger.info("Stopping all agents (%d running across %d tenants)...", total, len(self._tenants))

        for tid, tenant_agents in list(self._tenants.items()):
            for aid in list(tenant_agents.keys()):
                try:
                    await tenant_agents[aid].stop()
                except Exception as e:
                    logger.error("Error stopping %s/%s: %s", tid, aid, e)

        self._tenants.clear()

    def list_loaded_agents(self, tenant_id: Optional[str] = None) -> list:
        effective = tenant_id or self._current_tenant_id()
        return list(self._tenants.get(effective, {}).keys())

    def is_agent_loaded(self, agent_id: str, tenant_id: Optional[str] = None) -> bool:
        effective = tenant_id or self._current_tenant_id()
        return agent_id in self._tenants.get(effective, {})

    async def reload_agent(self, agent_id: str, tenant_id: Optional[str] = None) -> bool:
        """Reload an agent (simplified — delegates to stop + get)."""
        effective = tenant_id or self._current_tenant_id()
        await self.stop_agent(agent_id, tenant_id=effective)
        await self.get_agent(agent_id, tenant_id=effective)
        return True

    async def preload_agent(self, agent_id: str, tenant_id: Optional[str] = None) -> bool:
        """Preload an agent during startup."""
        try:
            await self.get_agent(agent_id, tenant_id=tenant_id)
            return True
        except Exception as e:
            logger.error("Failed to pre-load agent '%s': %s", agent_id, e)
            return False

    async def start_all_configured_agents(self) -> dict:
        """Start all configured agents for the default tenant.

        Delegates to the real manager for the initial startup,
        then migrates the loaded agents into the tenant dict.
        """
        result = await self._real.start_all_configured_agents()
        # Migrate agents from real manager into our tenant dict
        tenant_id = "default"
        for agent_id, instance in list(self._real.agents.items()):
            self._tenant_agents(tenant_id)[agent_id] = instance
            logger.info(
                "[multi-tenant/manager] Migrated agent '%s': "
                "started=%s, channel_manager=%s",
                agent_id, instance._started,
                instance.channel_manager,
            )
        # Clear real manager's dict to avoid duplication
        self._real.agents.clear()
        logger.info(
            "[multi-tenant/manager] Migration complete: tenants=%s, "
            "default_agents=%s",
            list(self._tenants.keys()),
            list(self._tenants.get("default", {}).keys()),
        )
        return result

    def __repr__(self) -> str:
        total = sum(len(v) for v in self._tenants.values())
        return f"TenantAwareMultiAgentManager(tenants={list(self._tenants.keys())}, total_agents={total})"


def patch_manager(manager_instance) -> None:
    """Wrap the existing MultiAgentManager singleton with tenant awareness.

    Call this once after the manager is created in ``_app.py``.
    """
    global _wrapped_instance
    _wrapped_instance = TenantAwareMultiAgentManager(manager_instance)
    logger.info("[multi-tenant/manager] Wrapped MultiAgentManager with tenant isolation")


async def wrap_manager_for_tenant(app, manager_instance):
    """Lifespan hook callback for ``post_manager_init``.

    This is registered via :func:`qwenpaw_plugins.register_lifespan_hook`
    and called by ``_app.py``'s lifespan right after the MultiAgentManager
    is created, before ``start_all_configured_agents()``.

    Parameters
    ----------
    app:
        The FastAPI application instance (unused but required by hook signature).
    manager_instance:
        The freshly created MultiAgentManager.

    Returns
    -------
    The tenant-aware wrapper around the manager.
    """
    patch_manager(manager_instance)
    return get_wrapped_manager()


def register_manager_patch_hook() -> None:
    """Register a post-creation hook for the MultiAgentManager.

    .. deprecated::
        This function is kept for backward compatibility but is now a no-op.
        The actual hook registration is done in ``activate_multi_tenant()``
        via ``qwenpaw_plugins.register_lifespan_hook()``.
    """
    logger.info("[multi-tenant/manager] Manager patch hook registered (patching done in _app.py lifespan)")


def get_wrapped_manager():
    """Return the wrapped manager instance (or None if not yet patched)."""
    return _wrapped_instance
