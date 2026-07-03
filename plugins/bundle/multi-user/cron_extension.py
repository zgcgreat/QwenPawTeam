# -*- coding: utf-8 -*-
"""Cron extension: ensure cron/heartbeat tasks run with correct user context.

Problem
-------
Cron jobs and heartbeat tasks execute outside the normal HTTP request flow.
They are triggered by APScheduler or background tasks, and the
``current_user_id`` ContextVar is not set — it defaults to ``None`` (which
``get_current_user_id()`` maps to ``"default"``).

In multi-user mode, this means:

1. ``CronExecutor.execute()`` knows the ``target_user_id`` from the job
   spec but does NOT set the ContextVar before calling
   ``workspace.stream_query()``.
2. ``run_heartbeat_once()`` hard-codes ``"main"`` as the user ID and
   does NOT set the ContextVar either.

If any downstream code inside the cron/heartbeat execution path reads
``get_current_user_id()`` (e.g. ``load_config()``, ``UserAwarePath``,
``UserAwareLogPath``), it will see ``"default"`` instead of the actual
target user — potentially reading/writing the wrong user's data.

Solution
--------
We monkey-patch ``CronExecutor.execute`` and ``run_heartbeat_once`` to
set the ``current_user_id`` ContextVar at the start of execution and
clear it on exit.  This is done via a simple wrapper that:

1. Reads the target user ID from the job spec / arguments.
2. Sets ``current_user_id`` and ``agent_context._current_user_id``.
3. Sets ``workspace_dir`` for config resolution.
4. Yields to the original function.
5. Cleans up the ContextVars in a ``finally`` block.

This approach follows the project's "minimum upstream change" principle:
no upstream files are modified.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Saved originals for unpatch
_original_cron_execute = None
_original_heartbeat_once = None


def _set_user_context(user_id: Optional[str]) -> Optional[str]:
    """Set the user ContextVar for the current async/task context.

    Returns the previous user_id (for restoration), or None if
    user_id was not set or is "default".
    """
    if not user_id or user_id == "default" or user_id == "cron" or user_id == "main":
        return None

    from user_context import set_current_user_id, get_current_user_id
    previous = get_current_user_id()

    set_current_user_id(user_id)

    # Also set the upstream agent_context ContextVar
    try:
        from qwenpaw.app.agent_context import (
            set_current_user_id as _set_agent_ctx_user_id,
        )
        _set_agent_ctx_user_id(user_id)
    except ImportError:
        pass

    # Set workspace dir for config resolution
    try:
        from qwenpaw.config.context import set_current_workspace_dir
        from auth_extension import get_user_working_dir

        user_dir = get_user_working_dir(user_id)
        set_current_workspace_dir(user_dir)
    except ImportError:
        pass

    return previous


def _clear_user_context(previous: Optional[str]) -> None:
    """Restore the user ContextVar to its previous value."""
    from user_context import set_current_user_id

    if previous and previous != "default":
        set_current_user_id(previous)
    else:
        set_current_user_id(None)

    try:
        from qwenpaw.app.agent_context import (
            set_current_user_id as _set_agent_ctx_user_id,
        )
        if previous and previous != "default":
            _set_agent_ctx_user_id(previous)
        else:
            _set_agent_ctx_user_id(None)
    except ImportError:
        pass


async def _wrapped_cron_execute(self, job):
    """Wrapper for CronExecutor.execute that sets user ContextVar."""
    target_user_id = job.dispatch.target.user_id

    if target_user_id:
        previous = _set_user_context(target_user_id)
        try:
            return await _original_cron_execute(self, job)
        finally:
            _clear_user_context(previous)
    else:
        return await _original_cron_execute(self, job)


async def _wrapped_heartbeat_once(
    *,
    workspace,
    channel_manager,
    agent_id=None,
    workspace_dir=None,
):
    """Wrapper for run_heartbeat_once that sets user ContextVar.

    Heartbeat runs in the context of a specific workspace (agent).
    The workspace's agent_id can be used to derive the user context
    from the manager's user-to-agents mapping.
    """
    # Try to resolve the user_id from the workspace's agent_id.
    # The workspace is bound to a specific user through
    # UserAwareMultiAgentManager.
    user_id = None
    try:
        from manager_extension import get_wrapped_manager
        manager = get_wrapped_manager()
        if manager is not None:
            # Find which user owns this workspace
            for uid, agents in manager._users.items():
                if workspace in agents.values():
                    user_id = uid
                    break
    except Exception:
        pass

    if user_id:
        previous = _set_user_context(user_id)
        try:
            return await _original_heartbeat_once(
                workspace=workspace,
                channel_manager=channel_manager,
                agent_id=agent_id,
                workspace_dir=workspace_dir,
            )
        finally:
            _clear_user_context(previous)
    else:
        return await _original_heartbeat_once(
            workspace=workspace,
            channel_manager=channel_manager,
            agent_id=agent_id,
            workspace_dir=workspace_dir,
        )


def patch_cron_executor() -> None:
    """Monkey-patch CronExecutor.execute and run_heartbeat_once.

    This ensures that cron/heartbeat tasks run with the correct user
    ContextVar, so that load_config(), UserAwarePath, etc. resolve
    to the correct per-user paths.
    """
    global _original_cron_execute, _original_heartbeat_once

    # Patch CronExecutor.execute
    try:
        from qwenpaw.app.crons.executor import CronExecutor

        if _original_cron_execute is None:
            _original_cron_execute = CronExecutor.execute
            CronExecutor.execute = _wrapped_cron_execute
            logger.info(
                "[multi-user/cron] Patched CronExecutor.execute "
                "to set user ContextVar for cron jobs"
            )
    except ImportError:
        logger.debug(
            "[multi-user/cron] CronExecutor not available (upstream < v2.0?), "
            "skipping cron patch"
        )

    # Patch run_heartbeat_once
    try:
        from qwenpaw.app.crons import heartbeat as hb_module

        if _original_heartbeat_once is None:
            _original_heartbeat_once = hb_module.run_heartbeat_once
            hb_module.run_heartbeat_once = _wrapped_heartbeat_once
            logger.info(
                "[multi-user/cron] Patched run_heartbeat_once "
                "to set user ContextVar for heartbeat tasks"
            )
    except ImportError:
        logger.debug(
            "[multi-user/cron] heartbeat module not available, "
            "skipping heartbeat patch"
        )


def unpatch_cron_executor() -> None:
    """Restore original CronExecutor.execute and run_heartbeat_once."""
    global _original_cron_execute, _original_heartbeat_once

    if _original_cron_execute is not None:
        try:
            from qwenpaw.app.crons.executor import CronExecutor
            CronExecutor.execute = _original_cron_execute
        except ImportError:
            pass
        _original_cron_execute = None

    if _original_heartbeat_once is not None:
        try:
            from qwenpaw.app.crons import heartbeat as hb_module
            hb_module.run_heartbeat_once = _original_heartbeat_once
        except ImportError:
            pass
        _original_heartbeat_once = None

    logger.info("[multi-user/cron] Restored original cron/heartbeat functions")
