# -*- coding: utf-8 -*-
"""Tenant context utilities for multi-tenant support.

Provides a ContextVar-based mechanism to propagate the current tenant (user)
identity across async call chains without passing it explicitly through every
function signature.

.. note::

    This file is ported verbatim from CoPaw's ``copaw.app.tenant_context``
    with no functional changes.  It is self-contained and has zero dependencies
    on the upstream ``qwenpaw`` package.

Usage
-----
In middleware / router entry points::

    from qwenpaw_plugins.multi_tenant.tenant_context import set_current_tenant_id

    set_current_tenant_id(request.state.tenant_id)

In any downstream code that needs the tenant::

    from qwenpaw_plugins.multi_tenant.tenant_context import get_current_tenant_id

    tenant_id = get_current_tenant_id()   # e.g. "alice"
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

# ---------------------------------------------------------------------------
# Context variable
# ---------------------------------------------------------------------------

_current_tenant_id: ContextVar[Optional[str]] = ContextVar(
    "current_tenant_id",
    default=None,
)

# Special sentinel meaning "no auth / single-user mode"
_DEFAULT_TENANT = "default"


def set_current_tenant_id(tenant_id: Optional[str]) -> None:
    """Store *tenant_id* in the current async context.

    Args:
        tenant_id: Username / tenant identifier.  Pass ``None`` to clear.
    """
    _current_tenant_id.set(tenant_id)


def get_current_tenant_id() -> str:
    """Return the tenant ID for the current request context.

    Falls back to ``"default"`` when auth is disabled or no tenant has
    been set (e.g. CLI local calls that skip the auth middleware).

    Returns:
        str: Tenant identifier, never ``None``.
    """
    value = _current_tenant_id.get()
    return value if value else _DEFAULT_TENANT


def clear_current_tenant_id() -> None:
    """Clear the tenant context (useful between test cases)."""
    _current_tenant_id.set(None)
