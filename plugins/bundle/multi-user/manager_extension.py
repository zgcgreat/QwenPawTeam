# -*- coding: utf-8 -*-
"""Manager extension: wraps MultiAgentManager with per-user isolation.

Strategy
--------
Rather than replacing the original ``MultiAgentManager`` class, we create
a wrapper that adds a two-level dict ``(user_id → {agent_id → Workspace})``
on top of the original single-level dict.  The wrapper intercepts all public
methods and routes them to the correct user's sub-dict.

The global singleton is patched after it is first created.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Dict, Optional, Set

logger = logging.getLogger(__name__)

#: Reference to the original class (set during patching)
_OriginalManager = None

#: The wrapped singleton instance (replaces the global one)
_wrapped_instance = None


class UserAwareMultiAgentManager:
    """Wraps the original MultiAgentManager with per-user isolation.

    Each user gets their own dict of Workspace instances, so two users
    can have agents with the same name without interfering.

    **Fallback logic**: Each user first attempts to lazy-load their own
    Workspace from their own config (which points to the user's isolated
    workspace_dir).  Only when the user's config does NOT define the
    requested agent_id do we fall back to the "default" user's cached
    Workspace.  This prevents cross-user data leakage (e.g. files,
    sessions) while still allowing shared access when appropriate.
    """

    def __init__(self, real_manager):
        self._real = real_manager  # Original MultiAgentManager instance
        self._users: Dict[str, Dict] = {}
        self._lock = asyncio.Lock()
        self._cleanup_tasks: Set[asyncio.Task] = set()
        self._last_access: Dict[str, float] = {}  # user_id -> last access timestamp
        self._max_idle_users = 50  # Max idle users before eviction
        logger.debug("UserAwareMultiAgentManager initialized")

    # -- User helpers --

    def _user_agents(self, user_id: str) -> Dict:
        if user_id not in self._users:
            self._users[user_id] = {}
        self._touch_user(user_id)
        return self._users[user_id]

    def _touch_user(self, user_id: str) -> None:
        """Record that a user was recently active."""
        import time
        self._last_access[user_id] = time.time()

    def _evict_idle_users_if_needed(self) -> None:
        """Evict idle users if we exceed the limit.

        Only evicts users with no running agents (idle cache entries).
        Users with active agents are never evicted.
        """
        if len(self._users) <= self._max_idle_users:
            return

        import time
        now = time.time()
        # Sort by last access time (oldest first)
        idle_candidates = sorted(
            self._last_access.items(),
            key=lambda x: x[1],
        )

        evicted = 0
        target = len(self._users) - self._max_idle_users + 5  # evict a few extra
        for uid, last_access in idle_candidates:
            if evicted >= target:
                break
            # Don't evict the default user
            if uid == "default":
                continue
            # Don't evict users with running agents
            user_agents = self._users.get(uid, {})
            if any(
                getattr(ws, "_started", False)
                for ws in user_agents.values()
            ):
                continue
            # Evict this idle user
            user_agents.clear()
            self._users.pop(uid, None)
            self._last_access.pop(uid, None)
            evicted += 1

        if evicted > 0:
            logger.info(
                "[multi-user/manager] Evicted %d idle users, "
                "remaining: %d users",
                evicted, len(self._users),
            )

    def _current_user_id(self) -> str:
        try:
            from user_context import get_current_user_id
            return get_current_user_id()
        except Exception:
            return "default"

    # -- Backward compat: `.agents` returns current user's agents --

    @property
    def agents(self) -> Dict:
        return self._user_agents(self._current_user_id())

    # -- Core operations --

    async def get_agent(self, agent_id: str, user_id: Optional[str] = None):
        """Get agent by ID, isolated per user.

        Resolution order:
        1. Current user's cache
        2. Lazy-load from user's own config (creates user-specific workspace)
        3. "default" user's cache (shared agents fallback — only if user
           has no config for the requested agent_id)
        """
        effective = user_id or self._current_user_id()
        self._touch_user(effective)
        self._evict_idle_users_if_needed()
        user_agents = self._user_agents(effective)

        logger.info(
            "[multi-user/manager] get_agent: agent_id=%s, user_id_arg=%s, effective=%s",
            agent_id, user_id, effective,
        )

        # Step 1: Check current user's cache
        if agent_id in user_agents:
            return user_agents[agent_id]

        # Step 2: Lazy-load from user's own config (with lock)
        # This is the primary path — each user should get their own Workspace
        # with their own workspace_dir, even if the agent_id is the same as
        # the default user's.
        async with self._lock:
            # Double-check after acquiring lock
            if agent_id in user_agents:
                return user_agents[agent_id]

            logger.info(
                "[multi-user/manager] get_agent lazy-load: "
                "agent_id=%s, user=%s, existing_users=%s",
                agent_id, effective, list(self._users.keys()),
            )

            from qwenpaw.config.utils import load_config
            from config_extension import ensure_user_config_exists

            # Last-resort: ensure user config exists (covers legacy
            # registrations that pre-date config.json auto-generation).
            if effective != "default":
                ensure_user_config_exists(effective)

            config = load_config(user_id=effective)

            if agent_id not in config.agents.profiles:
                # The current user's config doesn't contain this agent_id.
                # Instead of returning a shared default Workspace instance
                # (which would leak data between users), create a new
                # Workspace for the current user with the default user's
                # agent config as a template.
                if effective != "default":
                    default_agents = self._users.get("default", {})
                    if agent_id in default_agents:
                        logger.info(
                            "[multi-user/manager] get_agent: "
                            "agent_id=%s not in user '%s' config, "
                            "creating isolated workspace from default template",
                            agent_id, effective,
                        )
                        # Copy the default user's agent config to this user
                        # so the new workspace gets the same settings but
                        # runs in its own isolated directory.
                        default_ws = default_agents[agent_id]
                        # Ensure user has a config entry for this agent
                        # (use the default's config as template)
                        from config_extension import get_user_working_dir, get_config_path
                        from qwenpaw.config.utils import save_config
                        from qwenpaw.config.config import AgentProfileRef
                        default_config = load_config(user_id="default")
                        default_ref = default_config.agents.profiles[agent_id]
                        # Create a user-specific agent ref pointing to the
                        # user's own workspace dir
                        user_dir = get_user_working_dir(effective)
                        user_agent_dir = str(
                            Path(user_dir) / "workspaces" / agent_id
                        )
                        user_ref = AgentProfileRef(
                            enabled=default_ref.enabled,
                            workspace_dir=user_agent_dir,
                        )
                        config.agents.profiles[agent_id] = user_ref
                        save_config(config, config_path=get_config_path(user_id=effective))
                        # Now recurse to create the actual Workspace
                        return await self.get_agent(agent_id, user_id=effective)

                raise ValueError(
                    f"Agent '{agent_id}' not found in config for "
                    f"user '{effective}'. Available: "
                    f"{list(config.agents.profiles.keys())}"
                )

            agent_ref = config.agents.profiles[agent_id]

            # Delegate to WorkspaceRegistry._create_workspace() so that
            # bootstrap_plugins_kwargs and app_services are injected
            # correctly, rather than constructing Workspace() directly.
            # NOTE: WorkspaceRegistry only exists in upstream v2.0+.
            # In v1.1.12, we fall back to direct Workspace() construction.
            workspace_registry = None
            try:
                from qwenpaw.app.workspace_registry import WorkspaceRegistry
                workspace_registry = getattr(
                    getattr(self._real, 'app_services', None),
                    '_workspace_registry', None,
                ) or (
                    isinstance(self._real, WorkspaceRegistry) and self._real
                )
            except ImportError:
                logger.debug(
                    "[multi-user/manager] workspace_registry not available "
                    "(upstream < v2.0), using direct Workspace construction"
                )

            if workspace_registry is not None:
                instance = workspace_registry._create_workspace(
                    agent_id=agent_id,
                    workspace_dir=agent_ref.workspace_dir,
                )
            else:
                # Fallback: no WorkspaceRegistry available (v1.1.12 or
                # edge cases during early startup).
                from qwenpaw.app.workspace.workspace import Workspace

                instance = Workspace(
                    agent_id=agent_id,
                    workspace_dir=agent_ref.workspace_dir,
                )

            await instance.start()
            instance.set_manager(self)
            user_agents[agent_id] = instance
            logger.info(
                "[multi-user/manager] Created agent '%s' for user '%s': "
                "started=%s, channel_manager=%s, workspace_dir=%s",
                agent_id, effective, instance._started,
                instance.channel_manager, instance.workspace_dir,
            )
            return instance

    async def stop_agent(self, agent_id: str, user_id: Optional[str] = None) -> bool:
        """Stop a specific agent instance."""
        effective = user_id or self._current_user_id()
        user_agents = self._user_agents(effective)

        if agent_id not in user_agents:
            return False

        instance = user_agents[agent_id]
        await instance.stop()
        del user_agents[agent_id]
        return True

    async def stop_all(self):
        """Stop all agents across all users."""
        total = sum(len(v) for v in self._users.values())
        logger.info("Stopping all agents (%d running across %d users)...", total, len(self._users))

        for uid, user_agents in list(self._users.items()):
            for aid in list(user_agents.keys()):
                try:
                    await user_agents[aid].stop()
                except Exception as e:
                    logger.error("Error stopping %s/%s: %s", uid, aid, e)

        self._users.clear()

    def list_loaded_agents(self, user_id: Optional[str] = None) -> list:
        effective = user_id or self._current_user_id()
        return list(self._users.get(effective, {}).keys())

    def is_agent_loaded(self, agent_id: str, user_id: Optional[str] = None) -> bool:
        effective = user_id or self._current_user_id()
        return agent_id in self._users.get(effective, {})

    async def reload_agent(self, agent_id: str, user_id: Optional[str] = None) -> bool:
        """Reload an agent (simplified — delegates to stop + get)."""
        effective = user_id or self._current_user_id()
        await self.stop_agent(agent_id, user_id=effective)
        await self.get_agent(agent_id, user_id=effective)
        return True

    async def preload_agent(self, agent_id: str, user_id: Optional[str] = None) -> bool:
        """Preload an agent during startup."""
        try:
            await self.get_agent(agent_id, user_id=user_id)
            return True
        except Exception as e:
            logger.error("Failed to pre-load agent '%s': %s", agent_id, e)
            return False

    async def start_all_configured_agents(self) -> dict:
        """Start all configured agents for the default user.

        Delegates to the real manager for the initial startup,
        then migrates the loaded agents into the user dict.
        """
        result = await self._real.start_all_configured_agents()
        # Migrate agents from real manager into our user dict
        user_id = "default"
        for agent_id, instance in list(self._real.agents.items()):
            self._user_agents(user_id)[agent_id] = instance
            logger.info(
                "[multi-user/manager] Migrated agent '%s': "
                "started=%s, channel_manager=%s",
                agent_id, instance._started,
                instance.channel_manager,
            )
        # Clear real manager's dict to avoid duplication
        self._real.agents.clear()
        logger.info(
            "[multi-user/manager] Migration complete: users=%s, "
            "default_agents=%s",
            list(self._users.keys()),
            list(self._users.get("default", {}).keys()),
        )
        return result

    def __repr__(self) -> str:
        total = sum(len(v) for v in self._users.values())
        return f"UserAwareMultiAgentManager(users={list(self._users.keys())}, total_agents={total})"


def patch_manager(manager_instance) -> None:
    """Wrap the existing MultiAgentManager singleton with user awareness.

    Call this once after the manager is created in ``_app.py``.
    """
    global _wrapped_instance
    _wrapped_instance = UserAwareMultiAgentManager(manager_instance)
    logger.info("[multi-user/manager] Wrapped MultiAgentManager with user isolation")


async def wrap_manager_for_user(app, manager_instance):
    """Lifespan hook callback for ``post_manager_init``.

    This is registered via :func:`qwenpaw.plugins.api.PluginApi.register_startup_hook`
    and called during ``_background_startup()`` right after the MultiAgentManager
    is created, before ``start_all_configured_agents()``.

    Parameters
    ----------
    app:
        The FastAPI application instance (unused but required by hook signature).
    manager_instance:
        The freshly created MultiAgentManager.

    Returns
    -------
    The user-aware wrapper around the manager.
    """
    patch_manager(manager_instance)
    return get_wrapped_manager()


def register_manager_patch_hook() -> None:
    """Register a post-creation hook for the MultiAgentManager.

    .. deprecated::
        This function is kept for backward compatibility but is now a no-op.
        The actual hook registration is done in ``activate_multi_user()``
        via ``PluginApi.register_startup_hook()``.
    """
    logger.info("[multi-user/manager] Manager patch hook registered (patching done in _app.py lifespan)")


def get_wrapped_manager():
    """Return the wrapped manager instance (or None if not yet patched)."""
    return _wrapped_instance
