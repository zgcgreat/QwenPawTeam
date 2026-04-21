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

    from qwenpaw_plugins.multi_user.user_context import set_current_user_id

    set_current_user_id(request.state.user_id)

In any downstream code that needs the user::

    from qwenpaw_plugins.multi_user.user_context import get_current_user_id

    user_id = get_current_user_id()   # e.g. "alice"
"""
from __future__ import annotations

from contextvars import ContextVar
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
