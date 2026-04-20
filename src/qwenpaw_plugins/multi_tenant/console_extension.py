# -*- coding: utf-8 -*-
"""Console router extension: make backend log reading AND writing tenant-aware.

Strategy
--------
The upstream ``console.py`` uses ``LOG_FILE_PATH`` (imported from
``qwenpaw.utils.logging``) to read the backend log.  This extension patches
that module-level attribute so that ``LOG_FILE_PATH.resolve()`` returns the
correct tenant-aware path.

On the write side, the global ``FileHandler`` is replaced with a
``TenantAwareFileHandler`` that inspects the ``current_tenant_id``
ContextVar on every ``emit()`` call.

Tenant Directory Layout
-----------------------
:::

    {WORKING_DIR}/                          <- ~/.qwenpaw
    ├── qwenpaw.log                         <- global / default log
    └── .qwenpaw.secret/tenants/
        └── {tenant_id}/
            └── qwenpaw.log                 <- per-tenant log
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
# Tenant-aware path wrapper (read side)
# ---------------------------------------------------------------------------


class TenantAwareLogPath:
    """A Path-like object that resolves to the correct tenant's log file.

    The upstream ``console.py`` only uses ``LOG_FILE_PATH.resolve()``,
    so this wrapper only needs to intercept ``resolve()`` and delegate
    everything else to the original ``Path`` object.
    """

    def __init__(self, original: Path) -> None:
        self._original = original

    def resolve(self, strict: bool = False) -> Path:
        """Resolve to tenant-aware log path or original.

        When a non-default tenant is active, returns the tenant-specific
        log file path; otherwise falls back to the original global path.
        """
        tenant_id = _resolve_tenant_id()
        if tenant_id and tenant_id != "default":
            from .auth_extension import get_tenant_secret_dir
            tenant_dir = get_tenant_secret_dir(tenant_id)
            tenant_dir.mkdir(parents=True, exist_ok=True)
            return (tenant_dir / self._original.name).resolve(strict)
        return self._original.resolve(strict)

    # Delegate all other attribute access to the original Path.
    # This ensures compatibility if upstream adds new usage patterns.
    def __getattr__(self, name: str):
        return getattr(self._original, name)

    def __repr__(self) -> str:
        return f"TenantAwareLogPath({self._original!r})"

    # Support Path-like operations (e.g. str() / comparisons)
    def __str__(self) -> str:
        return str(self._original)

    def __fspath__(self) -> str:
        return str(self._original)


def _resolve_tenant_id() -> str | None:
    """Return the current request's tenant ID, or None for default."""
    try:
        from .tenant_context import get_current_tenant_id
        tid = get_current_tenant_id()
        if tid and tid != "default":
            return tid
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Tenant-aware file handler (write side)
# ---------------------------------------------------------------------------

# Cache of open file handles per tenant: {tenant_id: file_handle}
_tenant_log_handles: dict[str, IO[str]] = {}


def _get_tenant_log_path(tenant_id: str, original_log_path: Path) -> Path:
    """Resolve the log file path for a given tenant."""
    from .auth_extension import get_tenant_secret_dir
    tenant_dir = get_tenant_secret_dir(tenant_id)
    tenant_dir.mkdir(parents=True, exist_ok=True)
    return tenant_dir / original_log_path.name


class TenantAwareFileHandler(logging.FileHandler):
    """A FileHandler that routes log writes to per-tenant files.

    On every ``emit()`` call, it checks the ``current_tenant_id``
    ContextVar.  If a non-default tenant is active, the record is
    written to the tenant's own log file.  Otherwise, the original
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
        """Emit a log record to the correct tenant's log file."""
        try:
            tenant_id = _resolve_tenant_id()
            if tenant_id:
                self._emit_to_tenant(record, tenant_id)
            else:
                # No tenant context -> write to original global log
                self._original_handler.emit(record)
        except Exception:
            # Fallback to original handler on any error
            self._original_handler.emit(record)

    def _emit_to_tenant(self, record: logging.LogRecord, tenant_id: str) -> None:
        """Write a log record to a tenant-specific log file."""
        tenant_log_path = _get_tenant_log_path(tenant_id, self._original_path)

        # Get or create a file handle for this tenant
        handle_key = f"{tenant_id}:{tenant_log_path}"
        fh = _tenant_log_handles.get(handle_key)

        if fh is None or fh.closed:
            try:
                tenant_log_path.parent.mkdir(parents=True, exist_ok=True)
                fh = open(tenant_log_path, "a", encoding="utf-8")
                _tenant_log_handles[handle_key] = fh
            except OSError:
                # Can't open tenant log file — fall back to global
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
        """Close all tenant log handles and the original handler."""
        for fh in _tenant_log_handles.values():
            try:
                if not fh.closed:
                    fh.close()
            except Exception:
                pass
        _tenant_log_handles.clear()
        self._original_handler.close()
        super().close()


# ---------------------------------------------------------------------------
# Patch / unpatch
# ---------------------------------------------------------------------------

def patch_console_router() -> None:
    """Replace the ``LOG_FILE_PATH`` reference in ``console.py`` with a
    tenant-aware variant AND replace the global FileHandler with a
    ``TenantAwareFileHandler``.
    """
    global _original_log_file_path

    # --- Read-side patch: TenantAwareLogPath on console.LOG_FILE_PATH ---
    import qwenpaw.app.routers.console as console_module

    if _original_log_file_path is None:
        _original_log_file_path = console_module.LOG_FILE_PATH

    console_module.LOG_FILE_PATH = TenantAwareLogPath(_original_log_file_path)

    # --- Write-side patch: TenantAwareFileHandler ---
    _patch_log_handler()

    logger.info(
        "[multi-tenant/console] Replaced LOG_FILE_PATH in console router "
        "with tenant-aware TenantAwareLogPath and patched file handler"
    )


def _patch_log_handler() -> None:
    """Replace the global FileHandler with a TenantAwareFileHandler."""
    from qwenpaw.utils.logging import LOG_NAMESPACE

    log_logger = logging.getLogger(LOG_NAMESPACE)
    for i, handler in enumerate(log_logger.handlers):
        if isinstance(handler, logging.FileHandler) and not isinstance(
            handler, TenantAwareFileHandler
        ):
            # Replace the original FileHandler with our tenant-aware version
            tenant_handler = TenantAwareFileHandler(handler)
            log_logger.handlers[i] = tenant_handler
            logger.info(
                "[multi-tenant/console] Replaced FileHandler(%s) with "
                "TenantAwareFileHandler",
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

    logger.info("[multi-tenant/console] Restored original LOG_FILE_PATH and FileHandler")


def _unpatch_log_handler() -> None:
    """Restore the original FileHandler from within TenantAwareFileHandler."""
    from qwenpaw.utils.logging import LOG_NAMESPACE

    log_logger = logging.getLogger(LOG_NAMESPACE)
    for i, handler in enumerate(log_logger.handlers):
        if isinstance(handler, TenantAwareFileHandler):
            # Restore the original handler
            log_logger.handlers[i] = handler._original_handler
            # Close tenant handles
            for fh in _tenant_log_handles.values():
                try:
                    if not fh.closed:
                        fh.close()
                except Exception:
                    pass
            _tenant_log_handles.clear()
            return
