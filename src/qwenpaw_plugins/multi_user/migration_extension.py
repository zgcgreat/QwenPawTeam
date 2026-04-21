# -*- coding: utf-8 -*-
"""Migration extension: user workspace initialization helpers.

Ported from CoPaw's ``copaw.app.migration.ensure_user_default_agent_exists()``
with naming adapted for QwenPaw.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def ensure_user_default_agent_exists(user_id: str) -> None:
    """Ensure a user's default agent is fully initialized.

    Called lazily when a user first requests an agent.  Idempotent — safe
    to call on every request.

    Creates::

        WORKING_DIR/users/{user_parts}/
            config.json           ← per-user root config
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
        user_id: Composite user ID string.
    """
    from .auth_extension import get_user_working_dir, parse_user_id

    from qwenpaw.config.utils import load_config, save_config
    from qwenpaw.config.config import AgentProfileConfig, AgentProfileRef
    from qwenpaw.constant import WORKING_DIR

    user_working_dir = get_user_working_dir(user_id)
    user_config = load_config(user_id=user_id)

    user_default_workspace = user_working_dir / "workspaces" / "default"

    # Check if already initialized
    if "default" in user_config.agents.profiles:
        agent_ref = user_config.agents.profiles["default"]
        existing_ws = Path(agent_ref.workspace_dir).expanduser().resolve()
        if existing_ws == user_default_workspace.resolve():
            return  # Already pointing to user workspace

    # Create directory structure
    user_default_workspace.mkdir(parents=True, exist_ok=True)
    for sub in ("sessions", "memory", "active_skills", "customized_skills"):
        (user_default_workspace / sub).mkdir(exist_ok=True)

    # Create agent.json
    agent_config_path = user_default_workspace / "agent.json"
    if not agent_config_path.exists():
        default_agent_config = AgentProfileConfig(
            id="default",
            name="Default Agent",
            description=f"Default agent for user {user_id}",
            workspace_dir=str(user_default_workspace),
        )
        with open(agent_config_path, "w", encoding="utf-8") as f:
            json.dump(default_agent_config.model_dump(exclude_none=True), f, indent=2, ensure_ascii=False)
        logger.info("Created agent.json for user '%s': %s", user_id, agent_config_path)

    # Copy default markdown files if possible
    try:
        language = getattr(user_config.agents, "language", "zh") or "zh"
        _try_copy_md_files(language, user_default_workspace)
    except Exception as exc:
        logger.warning("Could not copy MD files for user '%s': %s", user_id, exc)

    # Ensure chats.json and jobs.json
    for fname, default in [("chats.json", {"version": 1, "chats": []}), ("jobs.json", {"version": 1, "jobs": []})]:
        fpath = user_default_workspace / fname
        if not fpath.exists():
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(default, f, indent=2, ensure_ascii=False)

    # Register in user config
    user_config.agents.profiles["default"] = AgentProfileRef(id="default", workspace_dir=str(user_default_workspace))
    if not user_config.agents.active_agent:
        user_config.agents.active_agent = "default"
    save_config(user_config, user_id=user_id)

    # Inject user info into PROFILE.md
    _inject_user_info_to_profile(user_default_workspace, user_id)

    logger.info("Initialized default agent for user '%s' at %s", user_id, user_default_workspace)


def _try_copy_md_files(language: str, workspace_dir: Path) -> None:
    """Attempt to copy default markdown files into workspace."""
    # Try to find source MD files from the package
    try:
        import qwenpaw.agents.utils as utils_module
        if hasattr(utils_module, 'copy_md_files'):
            utils_module.copy_md_files(language=language, skip_existing=True, workspace_dir=workspace_dir)
    except Exception:
        pass  # Non-critical


def _inject_user_info_to_profile(workspace_dir: Path, user_id: str) -> None:
    """Write user organization info into PROFILE.md."""
    from .constants import USER_FIELD_LABELS_ZH, USER_FIELD_LABELS_EN, USER_FIELDS
    from .auth_extension import parse_user_id

    profile_path = workspace_dir / "PROFILE.md"
    if not profile_path.exists():
        return

    fields = {}
    try:
        fields = parse_user_id(user_id) if "/" in user_id else {}
    except ValueError:
        pass

    content = profile_path.read_text(encoding="utf-8")
    is_zh = "\u7528\u6237\u8d44\u6599" in content or "\u8eab\u4efd" in content
    labels = USER_FIELD_LABELS_ZH if is_zh else USER_FIELD_LABELS_EN

    sentinel_zh = "\u7528\u6237\u4fe1\u606f"
    sentinel_en = "User Info"
    if sentinel_zh in content or sentinel_en in content:
        return

    if not fields:
        return

    header = sentinel_zh if is_zh else sentinel_en
    lines = [
        f"- **{labels[field]}：** {fields.get(field, '')}"
        for field in USER_FIELDS
    ]

    section = f"\n\n{header}\n\n" + "\n".join(lines) + "\n"
    updated = content.rstrip("\n") + section
    profile_path.write_text(updated, encoding="utf-8")
    logger.info("Injected user info into %s for '%s'", profile_path, user_id)
