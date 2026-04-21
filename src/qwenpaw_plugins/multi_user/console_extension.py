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
            from .auth_extension import get_user_secret_dir
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

    # Support Path-like operations (e.g. str() / comparisons)
    def __str__(self) -> str:
        return str(self._original)

    def __fspath__(self) -> str:
        return str(self._original)


def _resolve_user_id() -> str | None:
    """Return the current request's user ID, or None for default."""
    try:
        from .user_context import get_current_user_id
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


def _get_user_log_path(user_id: str, original_log_path: Path) -> Path:
    """Resolve the log file path for a given user."""
    from .auth_extension import get_user_secret_dir
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
