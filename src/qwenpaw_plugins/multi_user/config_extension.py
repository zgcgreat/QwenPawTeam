# -*- coding: utf-8 -*-
"""Config extensions: make load_config / save_config user-aware.

Strategy
--------
We monkey-patch the functions in ``qwenpaw.config.utils`` so that every
call site that uses ``load_config()`` or ``save_config()`` automatically
benefits from user routing without any upstream code changes.

The patched versions check:

1. Explicit ``user_id`` keyword argument (if passed).
2. The async context variable set by the auth middleware.
3. Falls back to the original root config (single-user mode).

User Directory Layout
--------------------
:::

    {WORKING_DIR}/
    ├── config.json              ← root (single-user) config
    └── users/
        └── {user_field_1}/{user_field_2}/.../
            └── config.json       ← per-user config

Each user gets an isolated sub-directory under ``users/``.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from qwenpaw.constant import WORKING_DIR

logger = logging.getLogger(__name__)

#: Subdirectory name for all user data
USERS_DIR_NAME = "users"

#: Keep references to the original functions so we can delegate.
_original_load_config = None
_original_save_config = None
_original_get_config_path = None


def get_user_base_dir() -> Path:
    """Return the base directory under which all user data lives.

    Returns:
        Path: ``{WORKING_DIR}/users/``
    """
    return WORKING_DIR / USERS_DIR_NAME


def get_user_working_dir(user_id: str) -> Path:
    """Return the working directory for a specific user.

    The user ID is expected to be a composite string like
    ``"field1/field2/..."`` (composite user ID).  Each component
    becomes a subdirectory level, providing natural filesystem isolation.

    Args:
        user_id: Composite user identifier (slash-separated).

    Returns:
        Path: ``{WORKING_DIR}/users/{user_id}/``
    """
    if not user_id or user_id == "default":
        return WORKING_DIR
    return get_user_base_dir() / user_id


def get_config_path(user_id: Optional[str] = None) -> Path:
    """Return the path to config.json, user-aware.

    Resolution order:

    1. If *user_id* is explicitly passed and not ``"default"`` → user config.
    2. If the async context has a non-default user set → user config.
    3. Otherwise → root config (single-user mode).

    Args:
        user_id: Composite user ID.  If ``None``, checks context.

    Returns:
        Path to the appropriate ``config.json``.
    """
    resolved = _resolve_user_id(user_id)
    if resolved and resolved != "default":
        return get_user_working_dir(resolved) / "config.json"
    # Fallback: use the original root path
    return WORKING_DIR.joinpath("config.json")


def _resolve_user_id(
    user_id: Optional[str] = None,
) -> Optional[str]:
    """Resolve effective user ID from argument → context variable → None.

    Args:
        user_id: Explicitly provided user ID.

    Returns:
        Resolved user ID, or ``None`` for default (single-user).
    """
    if user_id:
        return user_id
    try:
        from .user_context import get_current_user_id

        ctx_val = get_current_user_id()
        if ctx_val and ctx_val != "default":
            return ctx_val
    except Exception:
        pass
    return None


def load_config(config_path=None, **kwargs):
    """User-aware wrapper around the original ``load_config``.

    Accepts an optional ``user_id`` kwarg in addition to the original
    ``config_path`` positional argument.

    Delegates to the original function after resolving the config path.
    """
    user_id = kwargs.pop("user_id", None)
    if kwargs:
        logger.warning("Unexpected keyword arguments to load_config: %s", kwargs)

    if config_path is None:
        config_path = get_config_path(user_id)
    return _original_load_config(config_path=config_path)


def save_config(config, config_path=None, **kwargs):
    """User-aware wrapper around the original ``save_config``.

    Accepts an optional ``user_id`` kwarg.  Ensures the parent
    directory exists before saving.
    """
    user_id = kwargs.pop("user_id", None)
    if kwargs:
        logger.warning("Unexpected keyword arguments to save_config: %s", kwargs)

    if config_path is None:
        config_path = get_config_path(user_id)

    # Ensure the user directory structure exists
    config_path.parent.mkdir(parents=True, exist_ok=True)

    return _original_save_config(config, config_path=config_path)


def patch_config_utils() -> None:
    """Monkey-patch ``qwenpaw.config.utils`` with user-aware variants.

    This is called during plugin activation (Step 2 of
    :func:`activate_multi_user`).

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

    # Replace with user-aware versions
    utils_module.load_config = load_config
    utils_module.save_config = save_config
    if hasattr(utils_module, "get_config_path"):
        utils_module.get_config_path = get_config_path

    logger.info("[multi-user/config] Patched load_config, save_config, get_config_path")


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

    logger.info("[multi-user/config] Restored original config utils")


def ensure_user_config_exists(user_id: str):
    """Ensure a minimal config file exists for the given user.

    If the user's ``config.json`` does not exist yet, creates one with
    a ``default`` agent whose ``workspace_dir`` points to the user's
    isolated directory — not the global ``workspaces/default``.

    This is called by :func:`register_user` and
    :func:`ensure_user_workspace` during first-time user setup.

    Args:
        user_id: Composite user identifier.
    """
    config_path = get_config_path(user_id=user_id)
    if config_path.is_file():
        logger.debug(
            "[multi-user/config] User config already exists: %s",
            config_path,
        )
        return

    logger.info(
        "[multi-user/config] Creating initial config for user '%s' at %s",
        user_id,
        config_path,
    )

    # Ensure directory exists
    config_path.parent.mkdir(parents=True, exist_ok=True)

    from qwenpaw.config.config import Config, AgentProfileRef

    user_config = Config()

    # Override the default agent's workspace_dir to point at the user's
    # own working directory instead of the global one.
    user_ws_dir = get_user_working_dir(user_id) / "workspaces" / "default"
    user_ws_dir.mkdir(parents=True, exist_ok=True)
    user_config.agents.profiles["default"] = AgentProfileRef(
        id="default",
        workspace_dir=str(user_ws_dir),
    )

    _original_save_config(user_config, config_path=config_path)
    logger.info(
        "[multi-user/config] Created default config for user '%s' "
        "(workspace_dir=%s)",
        user_id,
        user_ws_dir,
    )
