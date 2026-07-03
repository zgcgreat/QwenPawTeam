# -*- coding: utf-8 -*-
"""User context utilities for multi-user support.

Provides a ContextVar-based mechanism to propagate the current user
identity across async call chains without passing it explicitly through every
function signature.

.. note::

    This file is ported verbatim from CoPaw's ``copaw.app.user_context``
    with no functional changes.  It is self-contained and has zero dependencies
    on the upstream ``qwenpaw`` package.

Usage
-----
In middleware / router entry points::

    from user_context import set_current_user_id

    set_current_user_id(request.state.user_id)

In any downstream code that needs the user::

    from user_context import get_current_user_id

    user_id = get_current_user_id()   # e.g. "alice"
"""
from __future__ import annotations

from contextvars import ContextVar, copy_context
from typing import Optional

# ---------------------------------------------------------------------------
# Context variable
# ---------------------------------------------------------------------------

_current_user_id: ContextVar[Optional[str]] = ContextVar(
    "current_user_id",
    default=None,
)

# Special sentinel meaning "no auth / single-user mode"
_DEFAULT_USER = "default"


def set_current_user_id(user_id: Optional[str]) -> None:
    """Store *user_id* in the current async context.

    Args:
        user_id: Username / user identifier.  Pass ``None`` to clear.
    """
    _current_user_id.set(user_id)


def get_current_user_id() -> str:
    """Return the user ID for the current request context.

    Falls back to ``"default"`` when auth is disabled or no user has
    been set (e.g. CLI local calls that skip the auth middleware).

    Returns:
        str: User identifier, never ``None``.
    """
    value = _current_user_id.get()
    return value if value else _DEFAULT_USER


def clear_current_user_id() -> None:
    """Clear the user context (useful between test cases)."""
    _current_user_id.set(None)


# ---------------------------------------------------------------------------
# Context-propagating run_in_executor patch
# ---------------------------------------------------------------------------

_original_run_in_executor = None


def _context_aware_run_in_executor(self, executor, func, *args):
    """Wrap run_in_executor to propagate ContextVars into the thread pool.

    Standard ``loop.run_in_executor()`` does NOT copy the current
    ContextVar context into the worker thread — unlike
    ``asyncio.to_thread()`` which does (PEP 567).  This means any
    code running inside ``run_in_executor`` (e.g. backup operations,
    coding mode config loading, browser control) would see
    ``_current_user_id`` as ``None`` and fall back to the default
    user — a cross-user data leak.

    This patch wraps the callable so it runs inside
    ``copy_context().run()``, which captures all ContextVars at the
    call site and replays them in the worker thread.
    """
    ctx = copy_context()
    # Return a coroutine that yields to the original run_in_executor
    # with a context-wrapped function
    return _original_run_in_executor(
        self, executor, lambda: ctx.run(func, *args),
    )


def patch_run_in_executor() -> None:
    """Monkey-patch asyncio event loop's run_in_executor to propagate ContextVars.

    This is called once during plugin startup.  It ensures that all
    upstream code using ``loop.run_in_executor()`` (which does NOT
    propagate ContextVars) behaves the same as ``asyncio.to_thread()``
    (which does).

    The patch is applied to ``asyncio.AbstractEventLoop.run_in_executor``
    so it affects all event loop implementations (Selector, Proactor, uvloop).
    """
    global _original_run_in_executor

    import asyncio

    if _original_run_in_executor is not None:
        return  # Already patched (idempotent)

    _original_run_in_executor = asyncio.AbstractEventLoop.run_in_executor
    asyncio.AbstractEventLoop.run_in_executor = _context_aware_run_in_executor

    import logging
    logging.getLogger(__name__).info(
        "[multi-user] Patched asyncio.AbstractEventLoop.run_in_executor "
        "to propagate ContextVars into thread pool workers"
    )


def unpatch_run_in_executor() -> None:
    """Restore the original run_in_executor (for testing / shutdown)."""
    global _original_run_in_executor

    if _original_run_in_executor is None:
        return

    import asyncio

    asyncio.AbstractEventLoop.run_in_executor = _original_run_in_executor
    _original_run_in_executor = None

    import logging
    logging.getLogger(__name__).info(
        "[multi-user] Restored original run_in_executor"
    )
