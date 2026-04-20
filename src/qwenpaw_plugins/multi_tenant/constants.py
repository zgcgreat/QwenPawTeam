# -*- coding: utf-8 -*-
"""Plugin-level constants for the multi-tenant system.

Centralizes all magic strings, environment variable names, and configuration
defaults so they can be changed in one place and to keep them distinct from
the upstream ``qwenpaw.*`` namespace.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------

#: Enable built-in HMAC authentication (standalone mode).
ENV_AUTH_ENABLED = "QWENPAW_AUTH_ENABLED"

#: Enable multi-tenant routing (independent of auth).
ENV_MULTI_TENANT_ENABLED = "QWENPAW_MULTI_TENANT_ENABLED"

#: Custom token parser module path (dotted Python path).
ENV_TOKEN_PARSER_MODULE = "QWENPAW_TOKEN_PARSER_MODULE"

#: Auto-register admin user fields (five-tuple).
ENV_AUTH_SYSID = "QWENPAW_AUTH_SYSID"
ENV_AUTH_BRANCHID = "QWENPAW_AUTH_BRANCHID"
ENV_AUTH_VORGCODE = "QWENPAW_AUTH_VORGCODE"
ENV_AUTH_SAPID = "QWENPAW_AUTH_SAPID"
ENV_AUTH_POSITIONID = "QWENPAW_AUTH_POSITIONID"
ENV_AUTH_PASSWORD = "QWENPAW_AUTH_PASSWORD"

# Legacy env vars (backward compat with CoPaw-style configs)
ENV_AUTH_USERNAME = "QWENPAW_AUTH_USERNAME"

# ---------------------------------------------------------------------------
# Tenant identity fields
# ---------------------------------------------------------------------------

#: The five fields that uniquely identify a tenant.
TENANT_FIELDS = ("sysId", "branchId", "vorgCode", "sapId", "positionId")

#: Separator used when composing a composite tenant ID.
TENANT_ID_SEPARATOR = "/"

#: Default tenant ID used when auth is disabled (single-user fallback).
DEFAULT_TENANT_ID = "default"

# ---------------------------------------------------------------------------
# Auth defaults
# ---------------------------------------------------------------------------

#: Token validity in seconds (7 days).
TOKEN_EXPIRY_SECONDS = 7 * 24 * 3600

# ---------------------------------------------------------------------------
# Public paths (no authentication required)
# ---------------------------------------------------------------------------

PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        # Plugin auth endpoints (mounted under /api prefix, same as upstream)
        "/api/auth/login",
        "/api/auth/status",
        "/api/auth/verify",
        "/api/auth/resolve-tenant",
        "/api/auth/init-workspace",
        # Upstream auth endpoints
        "/api/auth/register",
        # Public app pages
        "/api/version",
        "/",
        "/login",
        "/console",
        "/console/",
        "/api/settings/language",
    },
)

PUBLIC_PREFIXES: tuple[str, ...] = (
    "/assets/",
    "/logo.png",
    "/dark-logo.png",
    "/qwenpaw-symbol.svg",
    "/qwenpaw-dark.png",
)

# ---------------------------------------------------------------------------
# Tenant field labels (for PROFILE.md injection)
# ---------------------------------------------------------------------------

TENANT_FIELD_LABELS_ZH = {
    "sysId": "\u7cfb\u7edf\u7f16\u53f7",       # 系统编号
    "branchId": "\u5206\u884c\u53f7",           # 分行号
    "vorgCode": "\u4e2d\u5fc3\u53f7",            # 中心号
    "sapId": "\u7528\u6237\u7f16\u53f7",         # 用户编号
    "positionId": "\u5c97\u4f4d\u7f16\u53f7",    # 岗位编号
}

TENANT_FIELD_LABELS_EN = {
    "sysId": "System ID",
    "branchId": "Branch ID",
    "vorgCode": "Center ID",
    "sapId": "User ID",
    "positionId": "Position ID",
}
