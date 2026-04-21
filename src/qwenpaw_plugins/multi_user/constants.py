# -*- coding: utf-8 -*-
"""Plugin-level constants for the multi-user system.

Centralizes all magic strings, environment variable names, and configuration
defaults so they can be changed in one place and to keep them distinct from
the upstream ``qwenpaw.*`` namespace.

**Configurable user fields** — the set of fields that uniquely identify a
user can be customized via the ``QWENPAW_USER_FIELDS`` environment variable.
Field labels (Chinese / English / Japanese / Russian) can be overridden via
``QWENPAW_USER_FIELD_LABELS_ZH``, ``QWENPAW_USER_FIELD_LABELS_EN``,
``QWENPAW_USER_FIELD_LABELS_JA``, and ``QWENPAW_USER_FIELD_LABELS_RU``.

When no environment variable is set, the default is a single ``username`` field.
"""
from __future__ import annotations

import json
import os
import logging
import re
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------

#: Enable built-in HMAC authentication (standalone mode).
ENV_AUTH_ENABLED = "QWENPAW_AUTH_ENABLED"

#: Enable multi-user routing (independent of auth).
ENV_MULTI_USER_ENABLED = "QWENPAW_MULTI_USER_ENABLED"

#: Custom token parser module path (dotted Python path).
ENV_TOKEN_PARSER_MODULE = "QWENPAW_TOKEN_PARSER_MODULE"

#: Auto-register admin user password.
ENV_AUTH_PASSWORD = "QWENPAW_AUTH_PASSWORD"

# Legacy env vars (backward compat with CoPaw-style configs)
ENV_AUTH_USERNAME = "QWENPAW_AUTH_USERNAME"

# ---------------------------------------------------------------------------
# User identity fields — configurable via environment variables
# ---------------------------------------------------------------------------

#: Environment variable for comma-separated user field names.
#: Example: ``QWENPAW_USER_FIELDS=orgId,deptId,userId``
ENV_USER_FIELDS = "QWENPAW_USER_FIELDS"

#: Environment variable for Chinese label overrides (JSON dict).
#: Example: ``QWENPAW_USER_FIELD_LABELS_ZH={"orgId":"机构编号","deptId":"部门编号"}``
ENV_USER_FIELD_LABELS_ZH = "QWENPAW_USER_FIELD_LABELS_ZH"

#: Environment variable for English label overrides (JSON dict).
#: Example: ``QWENPAW_USER_FIELD_LABELS_EN={"orgId":"Organization ID"}``
ENV_USER_FIELD_LABELS_EN = "QWENPAW_USER_FIELD_LABELS_EN"

#: Environment variable for Japanese label overrides (JSON dict).
#: Example: ``QWENPAW_USER_FIELD_LABELS_JA={"orgId":"組織ID"}``
ENV_USER_FIELD_LABELS_JA = "QWENPAW_USER_FIELD_LABELS_JA"

#: Environment variable for Russian label overrides (JSON dict).
#: Example: ``QWENPAW_USER_FIELD_LABELS_RU={"orgId":"Идентификатор организации"}``
ENV_USER_FIELD_LABELS_RU = "QWENPAW_USER_FIELD_LABELS_RU"

# ---------------------------------------------------------------------------
# Default values (used when env vars are not set)
# ---------------------------------------------------------------------------

_DEFAULT_USER_FIELDS: Tuple[str, ...] = ("username",)

_DEFAULT_LABELS_ZH: Dict[str, str] = {
    "username": "用户名",
    "password": "密码",
    "orgId": "机构编号",
    "deptId": "部门编号",
    "userId": "用户编号",
    "tenantId": "租户编号",
    "companyId": "公司编号",
}

_DEFAULT_LABELS_EN: Dict[str, str] = {
    "username": "Username",
    "password": "Password",
    "orgId": "Organization ID",
    "deptId": "Department ID",
    "userId": "User ID",
    "tenantId": "Tenant ID",
    "companyId": "Company ID",
}

_DEFAULT_LABELS_JA: Dict[str, str] = {
    "username": "ユーザー名",
    "password": "パスワード",
    "orgId": "組織ID",
    "deptId": "部門ID",
    "userId": "ユーザーID",
    "tenantId": "テナントID",
    "companyId": "会社ID",
}

_DEFAULT_LABELS_RU: Dict[str, str] = {
    "username": "Имя пользователя",
    "password": "Пароль",
    "orgId": "ID организации",
    "deptId": "ID отдела",
    "userId": "ID пользователя",
    "tenantId": "ID арендатора",
    "companyId": "ID компании",
}


def _parse_user_fields() -> Tuple[str, ...]:
    """Read ``QWENPAW_USER_FIELDS`` env var and return a tuple of field names.

    Falls back to the default (``username``) if the env var is absent or empty.
    Invalid values are logged and ignored.
    """
    raw = os.environ.get(ENV_USER_FIELDS, "").strip()
    if not raw:
        return _DEFAULT_USER_FIELDS

    fields = tuple(f.strip() for f in raw.split(",") if f.strip())
    if not fields:
        logger.warning(
            "QWENPAW_USER_FIELDS env var is set but empty after parsing; "
            "falling back to defaults."
        )
        return _DEFAULT_USER_FIELDS

    logger.info("User fields configured from env: %s", fields)
    return fields


def _parse_field_labels(
    env_var: str,
    default_labels: Dict[str, str],
    user_fields: Tuple[str, ...],
    *,
    auto_fallback: bool = True,
) -> Dict[str, str]:
    """Read a label-map env var (JSON) and merge with defaults.

    For any field that doesn't have a label in the env var, the default
    label dict is consulted.  When ``auto_fallback=True`` (used for ZH/EN),
    fields without any label get an auto-generated English-style name
    (e.g. ``"orgId"`` → ``"Org Id"``).  When ``auto_fallback=False``
    (used for JA/RU), unknown fields are **omitted** so the frontend
    can fall back to its own i18n translations instead of showing an
    English auto-generated name in a non-English locale.
    """
    raw = os.environ.get(env_var, "").strip()
    overrides: Dict[str, str] = {}
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                overrides = {str(k): str(v) for k, v in parsed.items()}
            else:
                logger.warning(
                    "%s env var is not a JSON dict, ignoring", env_var
                )
        except json.JSONDecodeError:
            logger.warning(
                "%s env var is not valid JSON, ignoring", env_var
            )

    labels = {}
    for field in user_fields:
        if field in overrides:
            labels[field] = overrides[field]
        elif field in default_labels:
            labels[field] = default_labels[field]
        elif auto_fallback:
            # Auto-generate: "orgId" → "Org Id"
            name = field[0].upper() + field[1:]
            # Insert space before uppercase letters: "OrgId" → "Org Id"
            name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
            labels[field] = name
        # When auto_fallback=False and no label found, omit the key
        # so frontend i18n can provide the translation instead
    return labels


# ---------------------------------------------------------------------------
# Resolved configuration (read once at import time, can be refreshed)
# ---------------------------------------------------------------------------

USER_FIELDS: Tuple[str, ...] = _parse_user_fields()

#: Separator used when composing a composite user ID.
USER_ID_SEPARATOR = "/"

#: Default user ID used when auth is disabled (single-user fallback).
DEFAULT_USER_ID = "default"

USER_FIELD_LABELS_ZH: Dict[str, str] = _parse_field_labels(
    ENV_USER_FIELD_LABELS_ZH, _DEFAULT_LABELS_ZH, USER_FIELDS,
)

USER_FIELD_LABELS_EN: Dict[str, str] = _parse_field_labels(
    ENV_USER_FIELD_LABELS_EN, _DEFAULT_LABELS_EN, USER_FIELDS,
)

# JA/RU: no auto-fallback for unknown fields — omit them so frontend i18n
# can provide proper translations instead of showing English auto-generated names.
USER_FIELD_LABELS_JA: Dict[str, str] = _parse_field_labels(
    ENV_USER_FIELD_LABELS_JA, _DEFAULT_LABELS_JA, USER_FIELDS,
    auto_fallback=False,
)

USER_FIELD_LABELS_RU: Dict[str, str] = _parse_field_labels(
    ENV_USER_FIELD_LABELS_RU, _DEFAULT_LABELS_RU, USER_FIELDS,
    auto_fallback=False,
)


def get_env_var_for_field(field_name: str) -> str:
    """Return the auto-registration env var name for a user field.

    Example: ``"orgId"`` → ``"QWENPAW_AUTH_ORGID"``
    """
    return f"QWENPAW_AUTH_{field_name.upper()}"


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
        "/api/auth/resolve-user",
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
