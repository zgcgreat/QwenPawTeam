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

    Patching levels
    ---------------
    Many modules hold local bindings to ``load_config`` / ``save_config``
    created via ``from qwenpaw.config import load_config`` at import time.
    Simply patching ``qwenpaw.config.utils`` does **not** update those
    bindings.  We therefore patch at three levels:

    1. ``qwenpaw.config.utils`` — the canonical implementation
    2. ``qwenpaw.config`` — the package-level re-export
    3. Key modules that hold local bindings — so no stale reference
       can bypass user routing (same approach as
       ``token_usage_extension.py``).
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

    # Level 1: canonical utils module
    utils_module.load_config = load_config
    utils_module.save_config = save_config
    if hasattr(utils_module, "get_config_path"):
        utils_module.get_config_path = get_config_path

    # Level 2: package-level re-export (qwenpaw.config.__init__)
    # ``from .utils import load_config`` creates a local binding in
    # ``qwenpaw.config``'s namespace that is NOT updated by patching
    # ``utils_module`` above.  We must update it explicitly so that
    # any ``from qwenpaw.config import load_config`` done *after*
    # this patch (e.g. function-level imports) picks up the
    # user-aware version.
    try:
        import qwenpaw.config as config_pkg
        config_pkg.load_config = load_config
        config_pkg.save_config = save_config
        if hasattr(config_pkg, "get_config_path"):
            config_pkg.get_config_path = get_config_path
    except ImportError:
        pass

    # Level 3: modules that hold local bindings via
    # ``from ...config import load_config`` at module level.
    # These are the modules involved in request handling where
    # user-aware config resolution is critical.
    _patch_local_bindings()

    logger.info(
        "[multi-user/config] Patched load_config, save_config, get_config_path "
        "(utils, config package, and local bindings)"
    )


#: Modules that import ``load_config`` / ``save_config`` from
#: ``qwenpaw.config`` at module level (creating local bindings that
#: bypass the module-level patch).  Only request-handling modules
#: need to be listed here — CLI commands and startup-only code can
#: safely use the root config.
_LOCAL_BINDING_MODULES: list[str] = [
    "qwenpaw.app._app",
    "qwenpaw.app.multi_agent_manager",
    "qwenpaw.app.routers.workspace",
    "qwenpaw.app.routers.tools",
    "qwenpaw.app.routers.config",
    "qwenpaw.app.routers.agents",
    "qwenpaw.app.routers.settings",
    "qwenpaw.app.routers.providers",
    "qwenpaw.app.routers.mcp",
    "qwenpaw.app.routers.backup",
    "qwenpaw.app.routers.plan",
    "qwenpaw.app.agent_context",
    "qwenpaw.app.runner.utils",
    "qwenpaw.app.runner.daemon_commands",
    "qwenpaw.app.auth",
    "qwenpaw.app.workspace.workspace",
    "qwenpaw.agents.prompt",
    "qwenpaw.agents.utils.message_processing",
    "qwenpaw.agents.memory.reme_light_memory_manager",
    "qwenpaw.agents.utils.audio_transcription",
    "qwenpaw.agents.tools.get_current_time",
    "qwenpaw.security.tool_guard.engine",
    "qwenpaw.security.tool_guard.utils",
    "qwenpaw.security.tool_guard.guardians.shell_evasion_guardian",
    "qwenpaw.security.tool_guard.guardians.rule_guardian",
    "qwenpaw.security.tool_guard.guardians.file_guardian",
    "qwenpaw.security.skill_scanner",
]


def _patch_local_bindings() -> None:
    """Patch local ``load_config`` / ``save_config`` bindings in key modules.

    Modules that do ``from qwenpaw.config import load_config`` at module
    level create a local name binding.  Replacing the function in the
    source module (``qwenpaw.config.utils``) does NOT update these local
    bindings.  We must therefore patch each module individually.
    """
    import importlib

    for mod_name in _LOCAL_BINDING_MODULES:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue

        patched = []
        if hasattr(mod, "load_config") and mod.load_config is not load_config:
            # Only patch if the binding still points to the original
            # (avoid overwriting a function-level import inside a def)
            if mod.load_config is _original_load_config or (
                # Fallback: check by qualified name for cases where the
                # original reference is the same function object.
                hasattr(mod.load_config, "__module__")
                and mod.load_config.__module__ == "qwenpaw.config.utils"
                and mod.load_config.__qualname__ == "load_config"
            ):
                mod.load_config = load_config
                patched.append("load_config")

        if hasattr(mod, "save_config") and mod.save_config is not save_config:
            if mod.save_config is _original_save_config or (
                hasattr(mod.save_config, "__module__")
                and mod.save_config.__module__ == "qwenpaw.config.utils"
                and mod.save_config.__qualname__ == "save_config"
            ):
                mod.save_config = save_config
                patched.append("save_config")

        if hasattr(mod, "get_config_path") and mod.get_config_path is not get_config_path:
            if (
                _original_get_config_path is not None
                and (
                    mod.get_config_path is _original_get_config_path
                    or (
                        hasattr(mod.get_config_path, "__module__")
                        and mod.get_config_path.__module__ == "qwenpaw.config.utils"
                    )
                )
            ):
                mod.get_config_path = get_config_path
                patched.append("get_config_path")

        if patched:
            logger.debug(
                "[multi-user/config] Patched local bindings in %s: %s",
                mod_name,
                ", ".join(patched),
            )


def unpatch_config_utils() -> None:
    """Restore original functions (for testing / deactivation)."""
    global _original_load_config, _original_save_config, _original_get_config_path

    if _original_load_config is None:
        return

    import importlib

    import qwenpaw.config.utils as utils_module

    # Level 1: utils module
    utils_module.load_config = _original_load_config
    utils_module.save_config = _original_save_config
    if (
        _original_get_config_path is not None
        and hasattr(utils_module, "get_config_path")
    ):
        utils_module.get_config_path = _original_get_config_path

    # Level 2: config package
    try:
        import qwenpaw.config as config_pkg
        config_pkg.load_config = _original_load_config
        config_pkg.save_config = _original_save_config
        if hasattr(config_pkg, "get_config_path") and _original_get_config_path is not None:
            config_pkg.get_config_path = _original_get_config_path
    except ImportError:
        pass

    # Level 3: local bindings
    for mod_name in _LOCAL_BINDING_MODULES:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        if hasattr(mod, "load_config"):
            mod.load_config = _original_load_config
        if hasattr(mod, "save_config"):
            mod.save_config = _original_save_config
        if hasattr(mod, "get_config_path") and _original_get_config_path is not None:
            mod.get_config_path = _original_get_config_path

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
