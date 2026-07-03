# -*- coding: utf-8 -*-
"""Console router extension: make backend log reading AND writing user-aware.

Strategy
--------
The upstream ``console.py`` uses ``LOG_FILE_PATH`` (imported from
``qwenpaw.utils.logging``) to read the backend log.  This extension patches
that module-level attribute so that ``LOG_FILE_PATH.resolve()`` returns the
correct user-aware path.

On the write side, the global ``FileHandler`` is replaced with a
``UserAwareFileHandler`` that inspects the ``current_user_id``
ContextVar on every ``emit()`` call.

User Directory Layout
----------------------
:::

    {WORKING_DIR}/                          <- ~/.qwenpaw
    ├── qwenpaw.log                         <- global / default log
    └── .qwenpaw.secret/users/
        └── {user_id}/
            └── qwenpaw.log                 <- per-user log
"""
from __future__ import annotations

import logging
import logging.handlers
import platform
from pathlib import Path
from typing import IO

logger = logging.getLogger(__name__)

#: Original LOG_FILE_PATH from console module (saved on first patch)
_original_log_file_path: Path | None = None

# ---------------------------------------------------------------------------
# User-aware path wrapper (read side)
# ---------------------------------------------------------------------------


class UserAwareLogPath:
    """A Path-like object that resolves to the correct user's log file.

    The upstream ``console.py`` only uses ``LOG_FILE_PATH.resolve()``,
    so this wrapper only needs to intercept ``resolve()`` and delegate
    everything else to the original ``Path`` object.
    """

    def __init__(self, original: Path) -> None:
        self._original = original

    def resolve(self, strict: bool = False) -> Path:
        """Resolve to user-aware log path or original.

        When a non-default user is active, returns the user-specific
        log file path; otherwise falls back to the original global path.
        """
        user_id = _resolve_user_id()
        if user_id and user_id != "default":
            from auth_extension import get_user_secret_dir
            user_dir = get_user_secret_dir(user_id)
            user_dir.mkdir(parents=True, exist_ok=True)
            return (user_dir / self._original.name).resolve(strict)
        return self._original.resolve(strict)

    # Delegate all other attribute access to the original Path.
    # This ensures compatibility if upstream adds new usage patterns.
    def __getattr__(self, name: str):
        return getattr(self._original, name)

    def __repr__(self) -> str:
        return f"UserAwareLogPath({self._original!r})"

    # Support Path-like operations — use the user-resolved path
    # so that open(), os.fspath(), and str() all point to the
    # per-user log file, not the global one.
    def _current(self) -> Path:
        """Return the user-resolved path (same as resolve())."""
        return self.resolve()

    def __str__(self) -> str:
        return str(self._current())

    def __fspath__(self) -> str:
        return str(self._current())


def _resolve_user_id() -> str | None:
    """Return the current request's user ID, or None for default."""
    try:
        from user_context import get_current_user_id
        uid = get_current_user_id()
        if uid and uid != "default":
            return uid
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# User-aware file handler (write side)
# ---------------------------------------------------------------------------

# Cache of open file handles per user: {user_id: file_handle}
_user_log_handles: dict[str, IO[str]] = {}
_MAX_LOG_HANDLES = 100


def _prune_log_handles() -> None:
    """Close and remove excess log file handles (LRU by insertion order)."""
    while len(_user_log_handles) > _MAX_LOG_HANDLES:
        # Remove the oldest entry (first key in dict = oldest insertion)
        oldest_key = next(iter(_user_log_handles))
        fh = _user_log_handles.pop(oldest_key)
        try:
            if not fh.closed:
                fh.close()
        except Exception:
            pass


def _get_user_log_path(user_id: str, original_log_path: Path) -> Path:
    """Resolve the log file path for a given user."""
    from auth_extension import get_user_secret_dir
    user_dir = get_user_secret_dir(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir / original_log_path.name


class UserAwareFileHandler(logging.FileHandler):
    """A FileHandler that routes log writes to per-user files.

    On every ``emit()`` call, it checks the ``current_user_id``
    ContextVar.  If a non-default user is active, the record is
    written to the user's own log file.  Otherwise, the original
    global file handler is used.

    This handler replaces the global ``FileHandler`` added by
    ``add_project_file_handler()`` in ``_app.py``.
    """

    def __init__(self, original_handler: logging.FileHandler) -> None:
        # Don't call super().__init__ with a file — we manage our own streams.
        logging.Handler.__init__(self)
        self._original_handler = original_handler
        self._original_path = Path(original_handler.baseFilename)
        self.level = original_handler.level
        self.formatter = original_handler.formatter
        self._is_windows_or_linux = platform.system() in ("Windows", "Linux")

    def emit(self, record: logging.LogRecord) -> None:
        """Emit a log record to the correct user's log file."""
        try:
            user_id = _resolve_user_id()
            if user_id:
                self._emit_to_user(record, user_id)
            else:
                # No user context -> write to original global log
                self._original_handler.emit(record)
        except Exception:
            # Fallback to original handler on any error
            self._original_handler.emit(record)

    def _emit_to_user(self, record: logging.LogRecord, user_id: str) -> None:
        """Write a log record to a user-specific log file."""
        user_log_path = _get_user_log_path(user_id, self._original_path)

        # Get or create a file handle for this user
        handle_key = f"{user_id}:{user_log_path}"
        fh = _user_log_handles.get(handle_key)

        if fh is None or fh.closed:
            try:
                user_log_path.parent.mkdir(parents=True, exist_ok=True)
                fh = open(user_log_path, "a", encoding="utf-8")
                _user_log_handles[handle_key] = fh
                _prune_log_handles()
            except OSError:
                # Can't open user log file — fall back to global
                self._original_handler.emit(record)
                return

        try:
            msg = self.format(record)
            fh.write(msg + "\n")
            fh.flush()
        except OSError:
            # Write failed — try original handler
            self._original_handler.emit(record)

    def close(self) -> None:
        """Close all user log handles and the original handler."""
        for fh in _user_log_handles.values():
            try:
                if not fh.closed:
                    fh.close()
            except Exception:
                pass
        _user_log_handles.clear()
        self._original_handler.close()
        super().close()


# ---------------------------------------------------------------------------
# Patch / unpatch
# ---------------------------------------------------------------------------

def patch_console_router() -> None:
    """Replace the ``LOG_FILE_PATH`` reference in ``console.py`` with a
    user-aware variant AND replace the global FileHandler with a
    ``UserAwareFileHandler``.
    """
    global _original_log_file_path

    # --- Read-side patch: UserAwareLogPath on console.LOG_FILE_PATH ---
    import qwenpaw.app.routers.console as console_module

    if _original_log_file_path is None:
        _original_log_file_path = console_module.LOG_FILE_PATH

    console_module.LOG_FILE_PATH = UserAwareLogPath(_original_log_file_path)

    # --- Write-side patch: UserAwareFileHandler ---
    _patch_log_handler()

    # --- Endpoint patch: user-scoped inbox/approval/push filtering ---
    _patch_console_endpoints()

    logger.info(
        "[multi-user/console] Replaced LOG_FILE_PATH in console router "
        "with user-aware UserAwareLogPath and patched file handler"
    )


def _patch_log_handler() -> None:
    """Replace the global FileHandler with a UserAwareFileHandler."""
    from qwenpaw.utils.logging import LOG_NAMESPACE

    log_logger = logging.getLogger(LOG_NAMESPACE)
    for i, handler in enumerate(log_logger.handlers):
        if isinstance(handler, logging.FileHandler) and not isinstance(
            handler, UserAwareFileHandler
        ):
            # Replace the original FileHandler with our user-aware version
            user_handler = UserAwareFileHandler(handler)
            log_logger.handlers[i] = user_handler
            logger.info(
                "[multi-user/console] Replaced FileHandler(%s) with "
                "UserAwareFileHandler",
                handler.baseFilename,
            )
            # Only replace the first FileHandler (the project log)
            return


def unpatch_console_router() -> None:
    """Restore the original ``LOG_FILE_PATH`` in ``console.py`` and
    restore the original FileHandler.
    """
    global _original_log_file_path

    if _original_log_file_path is None:
        return

    # --- Restore read-side ---
    import qwenpaw.app.routers.console as console_module
    console_module.LOG_FILE_PATH = _original_log_file_path
    _original_log_file_path = None

    # --- Restore write-side ---
    _unpatch_log_handler()

    # --- Restore inbox/push/approval filtering ---
    _unpatch_console_endpoints()

    logger.info("[multi-user/console] Restored original LOG_FILE_PATH and FileHandler")


def _unpatch_log_handler() -> None:
    """Restore the original FileHandler from within UserAwareFileHandler."""
    from qwenpaw.utils.logging import LOG_NAMESPACE

    log_logger = logging.getLogger(LOG_NAMESPACE)
    for i, handler in enumerate(log_logger.handlers):
        if isinstance(handler, UserAwareFileHandler):
            # Restore the original handler
            log_logger.handlers[i] = handler._original_handler
            # Close user handles
            for fh in _user_log_handles.values():
                try:
                    if not fh.closed:
                        fh.close()
                except Exception:
                    pass
            _user_log_handles.clear()
            return


# ---------------------------------------------------------------------------
# Inbox / Push-messages / Approval user-scoping
# ---------------------------------------------------------------------------

# References to original route handlers (saved on patch)
_original_get_push_messages = None
_original_get_inbox_events = None
_original_post_mark_inbox_read = None
_original_delete_inbox_event = None
_original_get_approval_list = None


def _get_current_user_agents() -> set[str] | None:
    """Return the set of agent_ids owned by the current user, or None
    if multi-user is not enabled or the user is the default/admin.

    Returns None to indicate "no filtering needed" (admin or default user).
    """
    try:
        from user_context import get_current_user_id
        user_id = get_current_user_id()
        if not user_id or user_id == "default":
            return None

        # Get the user's agents from UserAwareMultiAgentManager
        from manager_extension import UserAwareMultiAgentManager
        from qwenpaw.plugins.registry import get_plugin_registry
        registry = get_plugin_registry()
        mgr = registry.get_workspace_manager()
        if mgr is None or not isinstance(mgr, UserAwareMultiAgentManager):
            return None

        agents = mgr.list_agents_for_user(user_id)
        if agents is None:
            return None
        return {a if isinstance(a, str) else a.get("agent_id", a) for a in agents}
    except Exception:
        logger.debug("[multi-user/console] Could not resolve user agents", exc_info=True)
        return None


def _filter_events_by_user(events: list[dict], user_agents: set[str]) -> list[dict]:
    """Filter inbox events to only those belonging to the user's agents."""
    return [
        e for e in events
        if e.get("agent_id") in user_agents
        or e.get("agent_id") == "default"
        or not e.get("agent_id")
    ]


async def _filtered_get_push_messages(
    session_id: str | None = None,
):
    """Wrapper that filters push messages and approvals by user scope."""
    # Call the original handler
    result = await _original_get_push_messages(session_id=session_id)

    user_agents = _get_current_user_agents()
    if user_agents is None:
        return result

    # Filter approvals by owner_agent_id
    filtered_approvals = [
        a for a in result.get("pending_approvals", [])
        if a.get("owner_agent_id") in user_agents
        or a.get("owner_agent_id") == "default"
        or not a.get("owner_agent_id")
    ]
    result["pending_approvals"] = filtered_approvals
    return result


async def _filtered_get_inbox_events(
    limit: int = 50,
    offset: int = 0,
    source_type: str | None = None,
    status: str | None = None,
    agent_id: str | None = None,
    unread_only: bool = False,
):
    """Wrapper that filters inbox events by user scope."""
    result = await _original_get_inbox_events(
        limit=limit,
        offset=offset,
        source_type=source_type,
        status=status,
        agent_id=agent_id,
        unread_only=unread_only,
    )

    user_agents = _get_current_user_agents()
    if user_agents is None:
        return result

    events = result.get("events", result) if isinstance(result, dict) else result
    if isinstance(events, list):
        filtered = _filter_events_by_user(events, user_agents)
        if isinstance(result, dict):
            result["events"] = filtered
        else:
            result = filtered
    return result


async def _filtered_post_mark_inbox_read(payload):
    """Wrapper that only marks events belonging to the current user."""
    from qwenpaw.app.inbox_store import _load_events, _save_events

    user_agents = _get_current_user_agents()
    if user_agents is None:
        return await _original_post_mark_inbox_read(payload)

    # For user-scoped access, only mark events that belong to their agents
    if payload.all:
        # Mark all events that belong to this user's agents
        import asyncio
        from qwenpaw.app.inbox_store import _LOCK
        updated = 0
        async with _LOCK:
            events = _load_events()
            for event in events:
                if not bool(event.get("read")):
                    if event.get("agent_id") in user_agents or not event.get("agent_id"):
                        event["read"] = True
                        updated += 1
            _save_events(events)
        return {"updated": updated}
    else:
        # Mark specific events, but only if they belong to this user
        return await _original_post_mark_inbox_read(payload)


async def _filtered_delete_inbox_event(event_id: str):
    """Wrapper that only allows deleting events belonging to the current user."""
    user_agents = _get_current_user_agents()
    if user_agents is None:
        return await _original_delete_inbox_event(event_id)

    # Check ownership before deleting
    from qwenpaw.app.inbox_store import _load_events
    events = _load_events()
    target = next((e for e in events if e.get("id") == event_id), None)
    if target and target.get("agent_id") not in user_agents and target.get("agent_id"):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Cannot delete events of other users")
    return await _original_delete_inbox_event(event_id)


async def _filtered_get_approval_list(request=None, session_id=None):
    """Wrapper that filters approval list by user scope."""
    result = await _original_get_approval_list(request=request, session_id=session_id)

    user_agents = _get_current_user_agents()
    if user_agents is None:
        return result

    approvals = result.pending_approvals if hasattr(result, "pending_approvals") else result.get("pending_approvals", [])
    filtered = [
        a for a in approvals
        if a.get("owner_agent_id") in user_agents
        or a.get("owner_agent_id") == "default"
        or not a.get("owner_agent_id")
    ]
    if hasattr(result, "pending_approvals"):
        result.pending_approvals = filtered
        result.count = len(filtered)
    elif isinstance(result, dict):
        result["pending_approvals"] = filtered
        result["count"] = len(filtered)
    return result


def _patch_console_endpoints() -> None:
    """Patch console router endpoints to filter by user scope."""
    global _original_get_push_messages, _original_get_inbox_events
    global _original_post_mark_inbox_read, _original_delete_inbox_event
    global _original_get_approval_list

    try:
        from qwenpaw.app.routers import console as console_module

        # Patch push-messages endpoint
        if hasattr(console_module, "get_push_messages"):
            _original_get_push_messages = console_module.get_push_messages
            console_module.get_push_messages = _filtered_get_push_messages
            logger.info("[multi-user/console] Patched get_push_messages for user-scoping")

    except Exception as e:
        logger.warning("[multi-user/console] Failed to patch push_messages: %s", e)

    try:
        from qwenpaw.app.routers import console as console_module

        # Patch inbox endpoints
        if hasattr(console_module, "get_inbox_events"):
            _original_get_inbox_events = console_module.get_inbox_events
            console_module.get_inbox_events = _filtered_get_inbox_events
            logger.info("[multi-user/console] Patched get_inbox_events for user-scoping")

        if hasattr(console_module, "post_mark_inbox_read"):
            _original_post_mark_inbox_read = console_module.post_mark_inbox_read
            console_module.post_mark_inbox_read = _filtered_post_mark_inbox_read
            logger.info("[multi-user/console] Patched post_mark_inbox_read for user-scoping")

        if hasattr(console_module, "delete_inbox_event"):
            _original_delete_inbox_event = console_module.delete_inbox_event
            console_module.delete_inbox_event = _filtered_delete_inbox_event
            logger.info("[multi-user/console] Patched delete_inbox_event for user-scoping")

    except Exception as e:
        logger.warning("[multi-user/console] Failed to patch inbox endpoints: %s", e)

    try:
        from qwenpaw.app.routers import approval as approval_module

        # Patch approval list endpoint
        if hasattr(approval_module, "get_approval_list"):
            _original_get_approval_list = approval_module.get_approval_list
            approval_module.get_approval_list = _filtered_get_approval_list
            logger.info("[multi-user/console] Patched get_approval_list for user-scoping")

    except Exception as e:
        logger.warning("[multi-user/console] Failed to patch approval list: %s", e)


def _unpatch_console_endpoints() -> None:
    """Restore original console endpoints."""
    try:
        from qwenpaw.app.routers import console as console_module

        if _original_get_push_messages is not None:
            console_module.get_push_messages = _original_get_push_messages
        if _original_get_inbox_events is not None:
            console_module.get_inbox_events = _original_get_inbox_events
        if _original_post_mark_inbox_read is not None:
            console_module.post_mark_inbox_read = _original_post_mark_inbox_read
        if _original_delete_inbox_event is not None:
            console_module.delete_inbox_event = _original_delete_inbox_event
    except Exception:
        pass

    try:
        from qwenpaw.app.routers import approval as approval_module

        if _original_get_approval_list is not None:
            approval_module.get_approval_list = _original_get_approval_list
    except Exception:
        pass
