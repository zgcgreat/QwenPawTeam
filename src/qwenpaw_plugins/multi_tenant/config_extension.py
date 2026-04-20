# -*- coding: utf-8 -*-
"""Config extensions: make load_config / save_config tenant-aware.

Strategy
--------
We monkey-patch the functions in ``qwenpaw.config.utils`` so that every
call site that uses ``load_config()`` or ``save_config()`` automatically
benefits from tenant routing without any upstream code changes.

The patched versions check:

1. Explicit ``tenant_id`` keyword argument (if passed).
2. The async context variable set by the auth middleware.
3. Falls back to the original root config (single-user mode).

Tenant Directory Layout
-----------------------
::

    {WORKING_DIR}/
    ├── config.json              ← root (single-user) config
    └── tenants/
        └── {sysId}/{branchId}/{vorgCode}/{sapId}/{positionId}/
            └── config.json       ← per-tenant config

Each tenant gets an isolated sub-directory under ``tenants/``.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from qwenpaw.constant import WORKING_DIR

logger = logging.getLogger(__name__)

#: Subdirectory name for all tenant data
TENANTS_DIR_NAME = "tenants"

#: Keep references to the original functions so we can delegate.
_original_load_config = None
_original_save_config = None
_original_get_config_path = None


def get_tenant_base_dir() -> Path:
    """Return the base directory under which all tenant data lives.

    Returns:
        Path: ``{WORKING_DIR}/tenants/``
    """
    return WORKING_DIR / TENANTS_DIR_NAME


def get_tenant_working_dir(tenant_id: str) -> Path:
    """Return the working directory for a specific tenant.

    The tenant ID is expected to be a composite string like
    ``"sysId/branchId/vorgCode/sapId/positionId"``.  Each component
    becomes a subdirectory level, providing natural filesystem isolation.

    Args:
        tenant_id: Composite tenant identifier (slash-separated).

    Returns:
        Path: ``{WORKING_DIR}/tenants/{tenant_id}/``
    """
    if not tenant_id or tenant_id == "default":
        return WORKING_DIR
    return get_tenant_base_dir() / tenant_id


def get_config_path(tenant_id: Optional[str] = None) -> Path:
    """Return the path to config.json, tenant-aware.

    Resolution order:

    1. If *tenant_id* is explicitly passed and not ``"default"`` → tenant config.
    2. If the async context has a non-default tenant set → tenant config.
    3. Otherwise → root config (single-user mode).

    Args:
        tenant_id: Composite tenant ID.  If ``None``, checks context.

    Returns:
        Path to the appropriate ``config.json``.
    """
    resolved = _resolve_tenant_id(tenant_id)
    if resolved and resolved != "default":
        return get_tenant_working_dir(resolved) / "config.json"
    # Fallback: use the original root path
    return WORKING_DIR.joinpath("config.json")


def _resolve_tenant_id(
    tenant_id: Optional[str] = None,
) -> Optional[str]:
    """Resolve effective tenant ID from argument → context variable → None.

    Args:
        tenant_id: Explicitly provided tenant ID.

    Returns:
        Resolved tenant ID, or ``None`` for default (single-user).
    """
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


def load_config(config_path=None, **kwargs):
    """Tenant-aware wrapper around the original ``load_config``.

    Accepts an optional ``tenant_id`` kwarg in addition to the original
    ``config_path`` positional argument.

    Delegates to the original function after resolving the config path.
    """
    tenant_id = kwargs.pop("tenant_id", None)
    if kwargs:
        logger.warning("Unexpected keyword arguments to load_config: %s", kwargs)

    if config_path is None:
        config_path = get_config_path(tenant_id)
    return _original_load_config(config_path=config_path)


def save_config(config, config_path=None, **kwargs):
    """Tenant-aware wrapper around the original ``save_config``.

    Accepts an optional ``tenant_id`` kwarg.  Ensures the parent
    directory exists before saving.
    """
    tenant_id = kwargs.pop("tenant_id", None)
    if kwargs:
        logger.warning("Unexpected keyword arguments to save_config: %s", kwargs)

    if config_path is None:
        config_path = get_config_path(tenant_id)

    # Ensure the tenant directory structure exists
    config_path.parent.mkdir(parents=True, exist_ok=True)

    return _original_save_config(config, config_path=config_path)


def patch_config_utils() -> None:
    """Monkey-patch ``qwenpaw.config.utils`` with tenant-aware variants.

    This is called during plugin activation (Step 2 of
    :func:`activate_multi_tenant`).

    .. note::
        Must be called **after** ``qwenpaw.config.utils`` has been imported
        at least once (so the module object exists), but before any request
        handler reads config.
    """
    global _original_load_config, _original_save_config, _original_get_config_path

    import qwenpaw.config.utils as utils_module

    # Save originals (only once — idempotent)
    if _original_load_config is None:
        _original_load_config = utils_module.load_config
        _original_save_config = utils_module.save_config
        _original_get_config_path = getattr(
            utils_module,
            "get_config_path",
            None,
        )

    # Replace with tenant-aware versions
    utils_module.load_config = load_config
    utils_module.save_config = save_config
    if hasattr(utils_module, "get_config_path"):
        utils_module.get_config_path = get_config_path

    logger.info("[multi-tenant/config] Patched load_config, save_config, get_config_path")


def unpatch_config_utils() -> None:
    """Restore original functions (for testing / deactivation)."""
    global _original_load_config, _original_save_config, _original_get_config_path

    if _original_load_config is None:
        return

    import qwenpaw.config.utils as utils_module

    utils_module.load_config = _original_load_config
    utils_module.save_config = _original_save_config
    if (
        _original_get_config_path is not None
        and hasattr(utils_module, "get_config_path")
    ):
        utils_module.get_config_path = _original_get_config_path

    logger.info("[multi-tenant/config] Restored original config utils")


def ensure_tenant_config_exists(tenant_id: str):
    """Ensure a minimal config file exists for the given tenant.

    If the tenant's ``config.json`` does not exist yet, creates one with
    a ``default`` agent whose ``workspace_dir`` points to the tenant's
    isolated directory — not the global ``workspaces/default``.

    This is called by :func:`register_user` and
    :func:`ensure_tenant_workspace` during first-time tenant setup.

    Args:
        tenant_id: Composite tenant identifier.
    """
    config_path = get_config_path(tenant_id=tenant_id)
    if config_path.is_file():
        logger.debug(
            "[multi-tenant/config] Tenant config already exists: %s",
            config_path,
        )
        return

    logger.info(
        "[multi-tenant/config] Creating initial config for tenant '%s' at %s",
        tenant_id,
        config_path,
    )

    # Ensure directory exists
    config_path.parent.mkdir(parents=True, exist_ok=True)

    from qwenpaw.config.config import Config, AgentProfileRef

    tenant_config = Config()

    # Override the default agent's workspace_dir to point at the tenant's
    # own working directory instead of the global one.
    tenant_ws_dir = get_tenant_working_dir(tenant_id) / "workspaces" / "default"
    tenant_ws_dir.mkdir(parents=True, exist_ok=True)
    tenant_config.agents.profiles["default"] = AgentProfileRef(
        id="default",
        workspace_dir=str(tenant_ws_dir),
    )

    _original_save_config(tenant_config, config_path=config_path)
    logger.info(
        "[multi-tenant/config] Created default config for tenant '%s' "
        "(workspace_dir=%s)",
        tenant_id,
        tenant_ws_dir,
    )
