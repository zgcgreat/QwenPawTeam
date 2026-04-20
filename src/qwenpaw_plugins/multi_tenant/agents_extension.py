# -*- coding: utf-8 -*-
"""Agents extension: makes workspace base directory tenant-aware.

Strategy
--------
We monkey-patch the ``_resolve_workspace_base_dir`` function in
``qwenpaw.app.routers.agents`` so that when a non-default tenant is
active, new agent workspaces are created under the tenant's working
directory instead of the global ``WORKING_DIR``.

This is the cleanest approach because:
1. The upstream ``agents.py`` only adds a 4-line helper function
   (``_resolve_workspace_base_dir``) that defaults to ``WORKING_DIR``.
2. All multi-tenant logic lives in this plugin file.
3. No import of multi-tenant modules from upstream code.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _tenant_aware_resolve_workspace_base_dir() -> Path:
    """Return the tenant-specific base directory for new workspaces.

    If a non-default tenant is active, return that tenant's working
    directory; otherwise fall back to the global ``WORKING_DIR``.
    """
    from qwenpaw.constant import WORKING_DIR

    try:
        from .tenant_context import get_current_tenant_id
        from .config_extension import get_tenant_working_dir

        tenant_id = get_current_tenant_id()
        if tenant_id and tenant_id != "default":
            return Path(get_tenant_working_dir(tenant_id))
    except Exception as exc:
        logger.debug(
            "[multi-tenant/agents] Failed to resolve tenant base dir, "
            "falling back to WORKING_DIR: %s",
            exc,
        )

    return WORKING_DIR


def patch_agents_router() -> None:
    """Patch the agents router's ``_resolve_workspace_base_dir`` function.

    Replace the default implementation (which always returns ``WORKING_DIR``)
    with the tenant-aware version defined above.
    """
    import qwenpaw.app.routers.agents as agents_module

    agents_module._resolve_workspace_base_dir = (
        _tenant_aware_resolve_workspace_base_dir
    )
    logger.info(
        "[multi-tenant/agents] Patched _resolve_workspace_base_dir "
        "with tenant-aware version",
    )
