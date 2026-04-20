# -*- coding: utf-8 -*-
"""Token usage extension: make TokenUsageManager tenant+agent-aware.

Strategy
--------
Instead of replacing the entire ``TokenUsageManager`` singleton, we
monkey-patch ``get_token_usage_manager()`` in the six places that
call it so that it returns a *per-tenant+agent* instance rather than
the global singleton.

Each (tenant, agent) combination gets its own ``TokenUsageManager``
instance whose ``_path`` points to::

    {SECRET_DIR}/tenants/{tenant_id}/{agent_id}/token_usage.json

For the ``"default"`` tenant (single-user / unauthenticated) the
original global manager is returned unchanged so existing behaviour is
fully preserved.

Directory Layout
----------------
:::

    {SECRET_DIR}/                          ← ~/.qwenpaw.secret
    ├── token_usage.json                   ← global (single-user) store
    └── tenants/
        └── {tenant_id}/
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

All six are patched so no stale local binding can bypass tenant routing.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Originals (saved once on first patch call — idempotent)
# ---------------------------------------------------------------------------

_original_get_token_usage_manager = None

# Per-(tenant, agent) manager cache: { "tenant_id:agent_id" -> TokenUsageManager }
_managers: dict[str, object] = {}
_managers_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Helpers
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


def _get_token_usage_path(tenant_id: str, agent_id: str) -> Path:
    """Return the token_usage.json path for *(tenant_id, agent_id)*.

    Layout: ``{SECRET_DIR}/tenants/{tenant_id}/{agent_id}/token_usage.json``
    """
    from .auth_extension import get_tenant_secret_dir
    return get_tenant_secret_dir(tenant_id) / agent_id / "token_usage.json"


# ---------------------------------------------------------------------------
# Tenant+agent-aware factory
# ---------------------------------------------------------------------------

def _tenant_agent_get_token_usage_manager():
    """Tenant+agent-aware replacement for ``get_token_usage_manager()``.

    - Non-default tenant  → cached per-(tenant, agent) ``TokenUsageManager``
                            whose ``_path`` resolves to the agent directory.
    - Default / no tenant → original global singleton (unchanged behaviour).
    """
    tenant_id = _resolve_tenant_id()

    if not tenant_id:
        # Single-user or default tenant: return the original singleton.
        return _original_get_token_usage_manager()

    agent_id = _resolve_agent_id()
    cache_key = f"{tenant_id}:{agent_id}"

    # Return (or create) a per-(tenant, agent) manager instance.
    if cache_key in _managers:
        return _managers[cache_key]

    with _managers_lock:
        # Double-checked locking
        if cache_key in _managers:
            return _managers[cache_key]

        from qwenpaw.token_usage.manager import TokenUsageManager

        mgr = TokenUsageManager.__new__(TokenUsageManager)
        # Manually initialise fields that __init__ would set, pointing
        # _path at the agent-specific file.
        mgr._path = _get_token_usage_path(tenant_id, agent_id)
        mgr._file_lock = asyncio.Lock()

        _managers[cache_key] = mgr
        logger.debug(
            "[multi-tenant/token_usage] Created manager for tenant '%s' "
            "agent '%s' at %s",
            tenant_id,
            agent_id,
            mgr._path,
        )

    return _managers[cache_key]


# ---------------------------------------------------------------------------
# Patch / unpatch
# ---------------------------------------------------------------------------

def patch_token_usage_manager() -> None:
    """Monkey-patch ``get_token_usage_manager`` with the tenant+agent-aware version.

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
    mgr_module.get_token_usage_manager = _tenant_agent_get_token_usage_manager

    # Level 2: package-level re-export
    try:
        import qwenpaw.token_usage as tu_pkg
        tu_pkg.get_token_usage_manager = _tenant_agent_get_token_usage_manager
    except ImportError:
        pass

    # Level 3: model_wrapper (has a local binding from `from .manager import ...`)
    try:
        import qwenpaw.token_usage.model_wrapper as wrapper_module
        wrapper_module.get_token_usage_manager = _tenant_agent_get_token_usage_manager
    except ImportError:
        pass

    # Level 4: HTTP router (has a local binding from `from ...token_usage import ...`)
    try:
        import qwenpaw.app.routers.token_usage as tu_router
        tu_router.get_token_usage_manager = _tenant_agent_get_token_usage_manager
    except ImportError:
        pass

    # Level 5: agent stats service (has a local binding from `from ..token_usage import ...`)
    try:
        import qwenpaw.agent_stats.service as stats_module
        stats_module.get_token_usage_manager = _tenant_agent_get_token_usage_manager
    except ImportError:
        pass

    # Level 6: token usage tool (has a local binding from `from ...token_usage import ...`)
    try:
        import qwenpaw.agents.tools.get_token_usage as tool_module
        tool_module.get_token_usage_manager = _tenant_agent_get_token_usage_manager
    except ImportError:
        pass

    logger.info(
        "[multi-tenant/token_usage] Patched manager, package, model_wrapper, "
        "router, agent_stats, tool (tenant+agent granularity)"
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

    _managers.clear()
    _original_get_token_usage_manager = None
    logger.info("[multi-tenant/token_usage] Restored original get_token_usage_manager")
