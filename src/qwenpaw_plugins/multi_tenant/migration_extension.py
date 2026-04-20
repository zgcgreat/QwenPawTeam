# -*- coding: utf-8 -*-
"""Migration extension: tenant workspace initialization helpers.

Ported from CoPaw's ``copaw.app.migration.ensure_tenant_default_agent_exists()``
with naming adapted for QwenPaw.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def ensure_tenant_default_agent_exists(tenant_id: str) -> None:
    """Ensure a tenant's default agent is fully initialized.

    Called lazily when a tenant first requests an agent.  Idempotent — safe
    to call on every request.

    Creates::

        WORKING_DIR/tenants/{tenant_parts}/
            config.json           ← per-tenant root config
            workspaces/
                default/
                    agent.json      ← AgentProfileConfig
                    chats.json
                    jobs.json
                    sessions/
                    memory/
                    active_skills/
                    customized_skills/

    Args:
        tenant_id: Composite tenant ID string.
    """
    from .auth_extension import get_tenant_working_dir, parse_tenant_id

    from qwenpaw.config.utils import load_config, save_config
    from qwenpaw.config.config import AgentProfileConfig, AgentProfileRef
    from qwenpaw.constant import WORKING_DIR

    tenant_working_dir = get_tenant_working_dir(tenant_id)
    tenant_config = load_config(tenant_id=tenant_id)

    tenant_default_workspace = tenant_working_dir / "workspaces" / "default"

    # Check if already initialized
    if "default" in tenant_config.agents.profiles:
        agent_ref = tenant_config.agents.profiles["default"]
        existing_ws = Path(agent_ref.workspace_dir).expanduser().resolve()
        if existing_ws == tenant_default_workspace.resolve():
            return  # Already pointing to tenant workspace

    # Create directory structure
    tenant_default_workspace.mkdir(parents=True, exist_ok=True)
    for sub in ("sessions", "memory", "active_skills", "customized_skills"):
        (tenant_default_workspace / sub).mkdir(exist_ok=True)

    # Create agent.json
    agent_config_path = tenant_default_workspace / "agent.json"
    if not agent_config_path.exists():
        default_agent_config = AgentProfileConfig(
            id="default",
            name="Default Agent",
            description=f"Default agent for tenant {tenant_id}",
            workspace_dir=str(tenant_default_workspace),
        )
        with open(agent_config_path, "w", encoding="utf-8") as f:
            json.dump(default_agent_config.model_dump(exclude_none=True), f, indent=2, ensure_ascii=False)
        logger.info("Created agent.json for tenant '%s': %s", tenant_id, agent_config_path)

    # Copy default markdown files if possible
    try:
        language = getattr(tenant_config.agents, "language", "zh") or "zh"
        _try_copy_md_files(language, tenant_default_workspace)
    except Exception as exc:
        logger.warning("Could not copy MD files for tenant '%s': %s", tenant_id, exc)

    # Ensure chats.json and jobs.json
    for fname, default in [("chats.json", {"version": 1, "chats": []}), ("jobs.json", {"version": 1, "jobs": []})]:
        fpath = tenant_default_workspace / fname
        if not fpath.exists():
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(default, f, indent=2, ensure_ascii=False)

    # Register in tenant config
    tenant_config.agents.profiles["default"] = AgentProfileRef(id="default", workspace_dir=str(tenant_default_workspace))
    if not tenant_config.agents.active_agent:
        tenant_config.agents.active_agent = "default"
    save_config(tenant_config, tenant_id=tenant_id)

    # Inject tenant info into PROFILE.md
    _inject_tenant_info_to_profile(tenant_default_workspace, tenant_id)

    logger.info("Initialized default agent for tenant '%s' at %s", tenant_id, tenant_default_workspace)


def _try_copy_md_files(language: str, workspace_dir: Path) -> None:
    """Attempt to copy default markdown files into workspace."""
    # Try to find source MD files from the package
    try:
        import qwenpaw.agents.utils as utils_module
        if hasattr(utils_module, 'copy_md_files'):
            utils_module.copy_md_files(language=language, skip_existing=True, workspace_dir=workspace_dir)
    except Exception:
        pass  # Non-critical


def _inject_tenant_info_to_profile(workspace_dir: Path, tenant_id: str) -> None:
    """Write tenant organization info into PROFILE.md."""
    from .constants import TENANT_FIELD_LABELS_ZH, TENANT_FIELD_LABELS_EN, TENANT_FIELDS
    from .auth_extension import parse_tenant_id

    profile_path = workspace_dir / "PROFILE.md"
    if not profile_path.exists():
        return

    fields = {}
    try:
        fields = parse_tenant_id(tenant_id) if "/" in tenant_id else {}
    except ValueError:
        pass

    content = profile_path.read_text(encoding="utf-8")
    is_zh = "\u7528\u6237\u8d44\u6599" in content or "\u8eab\u4efd" in content
    labels = TENANT_FIELD_LABELS_ZH if is_zh else TENANT_FIELD_LABELS_EN

    sentinel_zh = "\u7528\u6237\u4fe1\u606f"
    sentinel_en = "Tenant Info"
    if sentinel_zh in content or sentinel_en in content:
        return

    if not fields:
        return

    header = sentinel_zh if is_zh else sentinel_en
    lines = [
        f"- **{labels['sysId']}\uff1a** {fields.get('sysId', '')}",
        f"- **{labels['branchId']}\uff1a** {fields.get('branchId', '')}",
        f"- **{labels['vorgCode']}\uff1a** {fields.get('vorgCode', '')}",
        f"- **{labels['sapId']}\uff1a** {fields.get('sapId', '')}",
        f"- **{labels['positionId']}\uff1a** {fields.get('positionId', '')}",
    ]

    section = f"\n\n{header}\n\n" + "\n".join(lines) + "\n"
    updated = content.rstrip("\n") + section
    profile_path.write_text(updated, encoding="utf-8")
    logger.info("Injected tenant info into %s for '%s'", profile_path, tenant_id)
