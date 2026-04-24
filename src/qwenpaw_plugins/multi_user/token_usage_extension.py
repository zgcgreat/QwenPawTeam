# -*- coding: utf-8 -*-
"""Token usage extension: make TokenUsageManager user+agent-aware.

Strategy
--------
Instead of replacing the entire ``TokenUsageManager`` singleton, we
monkey-patch ``get_token_usage_manager()`` in the six places that
call it so that it returns a *per-user+agent* instance rather than
the global singleton.

Each (user, agent) combination gets its own ``TokenUsageManager``
instance whose ``_path`` points to::

    {SECRET_DIR}/users/{user_id}/{agent_id}/token_usage.json

For the ``"default"`` user (single-user / unauthenticated) the
original global manager is returned unchanged so existing behaviour is
fully preserved.

Directory Layout
----------------
:::

    {SECRET_DIR}/                          ← ~/.qwenpaw.secret
    ├── token_usage.json                   ← global (single-user) store
    └── users/
        └── {user_id}/
            └── {agent_id}/
                └── token_usage.json       ← per-agent store

Patching Levels
---------------
Six modules hold a reference to ``get_token_usage_manager``:

1. ``qwenpaw.token_usage.manager``       – the canonical implementation
2. ``qwenpaw.token_usage``               – package re-export
3. ``qwenpaw.token_usage.model_wrapper`` – records usage after each LLM call
4. ``qwenpaw.app.routers.token_usage``   – the ``GET /api/token-usage`` router
5. ``qwenpaw.agent_stats.service``       – the ``GET /api/agent-stats`` service
6. ``qwenpaw.agents.tools.get_token_usage`` – the ``get_token_usage`` tool

All six are patched so no stale local binding can bypass user routing.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Originals (saved once on first patch call — idempotent)
# ---------------------------------------------------------------------------

_original_get_token_usage_manager = None

# Per-(user, agent) manager cache: { "user_id:agent_id" -> TokenUsageManager }
_managers: dict[str, object] = {}
_managers_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Helpers
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


def _resolve_agent_id() -> str:
    """Return the current request's agent ID, or ``"default"``."""
    try:
        from qwenpaw.app.agent_context import get_current_agent_id
        aid = get_current_agent_id()
        if aid:
            return aid
    except Exception:
        pass
    return "default"


def _get_token_usage_path(user_id: str, agent_id: str) -> Path:
    """Return the token_usage.json path for *(user_id, agent_id)*.

    Layout: ``{SECRET_DIR}/users/{user_id}/{agent_id}/token_usage.json``
    """
    from .auth_extension import get_user_secret_dir
    return get_user_secret_dir(user_id) / agent_id / "token_usage.json"


# ---------------------------------------------------------------------------
# User+agent-aware factory
# ---------------------------------------------------------------------------

def _user_agent_get_token_usage_manager():
    """User+agent-aware replacement for ``get_token_usage_manager()``.

    - Non-default user  → cached per-(user, agent) ``TokenUsageManager``
                            whose ``_buffer._path`` resolves to the
                            agent-specific file.
    - Default / no user → original global singleton (unchanged behaviour).
    """
    user_id = _resolve_user_id()

    if not user_id:
        # Single-user or default user: return the original singleton.
        return _original_get_token_usage_manager()

    agent_id = _resolve_agent_id()
    cache_key = f"{user_id}:{agent_id}"

    # Return (or create) a per-(user, agent) manager instance.
    if cache_key in _managers:
        return _managers[cache_key]

    with _managers_lock:
        # Double-checked locking
        if cache_key in _managers:
            return _managers[cache_key]

        from qwenpaw.token_usage.manager import TokenUsageManager
        from qwenpaw.token_usage.buffer import TokenUsageBuffer

        path = _get_token_usage_path(user_id, agent_id)

        mgr = TokenUsageManager.__new__(TokenUsageManager)
        # Manually initialise fields that __init__ would set, pointing
        # the buffer at the agent-specific file.
        mgr._buffer = TokenUsageBuffer(path)
        mgr._flush_interval = 10

        # Start the buffer's background tasks (consumer + periodic
        # flush) so that enqueued events are persisted to disk.
        # This is safe because get_token_usage_manager() is always
        # called from within a request handler where the event loop
        # is running.
        try:
            mgr._buffer.start()
        except RuntimeError:
            # No running event loop — the buffer will remain idle.
            # Events will still be readable via get_merged_data()
            # (which combines disk cache + pending queue), but they
            # won't be persisted until the buffer is started later.
            logger.debug(
                "[multi-user/token_usage] Could not start buffer for "
                "user '%s' agent '%s' (no event loop); will start lazily",
                user_id,
                agent_id,
            )

        _managers[cache_key] = mgr
        logger.debug(
            "[multi-user/token_usage] Created manager for user '%s' "
            "agent '%s' at %s",
            user_id,
            agent_id,
            path,
        )

    return _managers[cache_key]


# ---------------------------------------------------------------------------
# Patch / unpatch
# ---------------------------------------------------------------------------

def patch_token_usage_manager() -> None:
    """Monkey-patch ``get_token_usage_manager`` with the user+agent-aware version.

    Patches six levels:

    1. ``qwenpaw.token_usage.manager``       – canonical module
    2. ``qwenpaw.token_usage``               – package re-export
    3. ``qwenpaw.token_usage.model_wrapper`` – records usage after LLM calls
    4. ``qwenpaw.app.routers.token_usage``   – HTTP router
    5. ``qwenpaw.agent_stats.service``       – agent stats service
    6. ``qwenpaw.agents.tools.get_token_usage`` – token usage tool
    """
    global _original_get_token_usage_manager

    import qwenpaw.token_usage.manager as mgr_module

    # Save original (idempotent)
    if _original_get_token_usage_manager is None:
        _original_get_token_usage_manager = mgr_module.get_token_usage_manager

    # Level 1: canonical manager module
    mgr_module.get_token_usage_manager = _user_agent_get_token_usage_manager

    # Level 2: package-level re-export
    try:
        import qwenpaw.token_usage as tu_pkg
        tu_pkg.get_token_usage_manager = _user_agent_get_token_usage_manager
    except ImportError:
        pass

    # Level 3: model_wrapper (has a local binding from `from .manager import ...`)
    try:
        import qwenpaw.token_usage.model_wrapper as wrapper_module
        wrapper_module.get_token_usage_manager = _user_agent_get_token_usage_manager
    except ImportError:
        pass

    # Level 4: HTTP router (has a local binding from `from ...token_usage import ...`)
    try:
        import qwenpaw.app.routers.token_usage as tu_router
        tu_router.get_token_usage_manager = _user_agent_get_token_usage_manager
    except ImportError:
        pass

    # Level 5: agent stats service (has a local binding from `from ..token_usage import ...`)
    try:
        import qwenpaw.agent_stats.service as stats_module
        stats_module.get_token_usage_manager = _user_agent_get_token_usage_manager
    except ImportError:
        pass

    # Level 6: token usage tool (has a local binding from `from ...token_usage import ...`)
    try:
        import qwenpaw.agents.tools.get_token_usage as tool_module
        tool_module.get_token_usage_manager = _user_agent_get_token_usage_manager
    except ImportError:
        pass

    logger.info(
        "[multi-user/token_usage] Patched manager, package, model_wrapper, "
        "router, agent_stats, tool (user+agent granularity)"
    )


def unpatch_token_usage_manager() -> None:
    """Restore original ``get_token_usage_manager`` (for testing / deactivation)."""
    global _original_get_token_usage_manager

    if _original_get_token_usage_manager is None:
        return

    import qwenpaw.token_usage.manager as mgr_module
    mgr_module.get_token_usage_manager = _original_get_token_usage_manager

    try:
        import qwenpaw.token_usage as tu_pkg
        tu_pkg.get_token_usage_manager = _original_get_token_usage_manager
    except ImportError:
        pass

    try:
        import qwenpaw.token_usage.model_wrapper as wrapper_module
        wrapper_module.get_token_usage_manager = _original_get_token_usage_manager
    except ImportError:
        pass

    try:
        import qwenpaw.app.routers.token_usage as tu_router
        tu_router.get_token_usage_manager = _original_get_token_usage_manager
    except ImportError:
        pass

    try:
        import qwenpaw.agent_stats.service as stats_module
        stats_module.get_token_usage_manager = _original_get_token_usage_manager
    except ImportError:
        pass

    try:
        import qwenpaw.agents.tools.get_token_usage as tool_module
        tool_module.get_token_usage_manager = _original_get_token_usage_manager
    except ImportError:
        pass

    # Stop all per-(user, agent) managers' buffers before clearing
    for mgr in _managers.values():
        try:
            if hasattr(mgr, "_buffer") and mgr._buffer is not None:
                # Best-effort stop; if no event loop, skip gracefully.
                try:
                    import asyncio
                    loop = asyncio.get_running_loop()
                    loop.create_task(mgr._buffer.stop())
                except RuntimeError:
                    pass
        except Exception:
            pass

    _managers.clear()
    _original_get_token_usage_manager = None
    logger.info("[multi-user/token_usage] Restored original get_token_usage_manager")
