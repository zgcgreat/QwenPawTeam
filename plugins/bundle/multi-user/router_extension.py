# -*- coding: utf-8 -*-
"""Router extension: additional multi-user auth API endpoints.

These endpoints are registered via ``app.include_router()`` during plugin
activation.  They coexist alongside the upstream's own routers under
different path prefixes.

Pydantic request/response models are created dynamically based on the
configured ``USER_FIELDS``, so the API schema adapts automatically when
the user isolation field set is changed via environment variables.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, create_model

from constants import (
    USER_FIELDS,
    USER_FIELD_LABELS_ZH,
    USER_FIELD_LABELS_EN,
    USER_FIELD_LABELS_JA,
    USER_FIELD_LABELS_RU,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["multi-user-auth"])


# ---------------------------------------------------------------------------
# Dynamic Request / Response models (built from USER_FIELDS)
# ---------------------------------------------------------------------------


def _build_login_request_model():
    """Create LoginRequest model dynamically from USER_FIELDS."""
    field_defs = {field: (str, ...) for field in USER_FIELDS}
    field_defs["password"] = (str, ...)
    return create_model("LoginRequest", **field_defs)


def _build_login_response_model():
    """Create LoginResponse model dynamically from USER_FIELDS."""
    field_defs = {"token": (str, ...), "user_id": (str, ...)}
    for field in USER_FIELDS:
        field_defs[field] = (str, ...)
    return create_model("LoginResponse", **field_defs)


def _build_init_workspace_request_model():
    """Create InitWorkspaceRequest model dynamically from USER_FIELDS."""
    field_defs = {field: (Optional[str], None) for field in USER_FIELDS}
    return create_model("InitWorkspaceRequest", **field_defs)


def _build_init_workspace_response_model():
    """Create InitWorkspaceResponse model dynamically from USER_FIELDS."""
    field_defs = {"user_id": (str, ...), "initialized": (bool, ...)}
    for field in USER_FIELDS:
        field_defs[field] = (str, ...)
    return create_model("InitWorkspaceResponse", **field_defs)


# Build models at module import time
LoginRequest = _build_login_request_model()
LoginResponse = _build_login_response_model()
InitWorkspaceRequest = _build_init_workspace_request_model()
InitWorkspaceResponse = _build_init_workspace_response_model()


class AuthStatusResponse(BaseModel):
    enabled: bool
    has_users: bool
    multi_user: bool
    user_fields: list[str] = list(USER_FIELDS)
    user_field_labels_zh: dict[str, str] = dict(USER_FIELD_LABELS_ZH)
    user_field_labels_en: dict[str, str] = dict(USER_FIELD_LABELS_EN)
    user_field_labels_ja: dict[str, str] = dict(USER_FIELD_LABELS_JA)
    user_field_labels_ru: dict[str, str] = dict(USER_FIELD_LABELS_RU)


class UpdateProfileRequest(BaseModel):
    current_password: str
    new_password: str | None = None


class DeleteUserRequest(BaseModel):
    user_id: str


# ---------------------------------------------------------------------------
# Public endpoints (no auth required)
# ---------------------------------------------------------------------------


@router.post("/login/")
@router.post("/login")
async def login(request: Request, req: LoginRequest):
    """Authenticate with user fields and password.

    Auto-registers if user does not exist.
    Includes rate limiting via upstream's LoginRateLimiter.
    """
    try:
        from auth_extension import (
            authenticate,
            build_user_id,
            is_auth_enabled,
            register_user,
            get_user_working_dir,
        )

        if not is_auth_enabled():
            empty_resp = {"token": "", "user_id": ""}
            for field in USER_FIELDS:
                empty_resp[field] = ""
            return LoginResponse(**empty_resp)

        # --- Rate limiting ---
        # NOTE: Upstream v1.1.12 does not have qwenpaw.app.rate_limiter.
        # We provide a simple in-memory login rate limiter as a fallback.
        client_ip = request.client.host if request.client else "unknown"
        username = getattr(req, USER_FIELDS[0], "") if USER_FIELDS else ""

        # Try upstream rate_limiter first (may exist in future versions)
        try:
            from qwenpaw.app.rate_limiter import rate_limiter as _rl
            if _rl.is_ip_limited(client_ip):
                raise HTTPException(
                    status_code=429,
                    detail="Too many login attempts from this IP. Try again later.",
                )
            if username and _rl.is_user_limited(username):
                raise HTTPException(
                    status_code=429,
                    detail="Too many login attempts for this user. Try again later.",
                )
            _record_fn = _rl.record_login_attempt
        except ImportError:
            # No upstream rate limiter — use simple local implementation
            from _rate_limiter import is_ip_limited, is_user_limited, record_login_attempt
            if is_ip_limited(client_ip):
                raise HTTPException(
                    status_code=429,
                    detail="Too many login attempts from this IP. Try again later.",
                )
            if username and is_user_limited(username):
                raise HTTPException(
                    status_code=429,
                    detail="Too many login attempts for this user. Try again later.",
                )
            _record_fn = record_login_attempt

        stripped = {}
        for field in USER_FIELDS:
            stripped[field] = getattr(req, field).strip()

        token = authenticate(req.password, **stripped)
        success = token is not None
        if not success:
            token = register_user(req.password, **stripped)
            success = token is not None
            if not success:
                _record_fn(client_ip, username, False)
                raise HTTPException(status_code=401, detail="Invalid credentials")

        _record_fn(client_ip, username, True)

        user_id = build_user_id(**stripped)

        # Initialize user workspace on login
        try:
            from migration_extension import ensure_user_default_agent_exists
            ensure_user_default_agent_exists(user_id)
        except Exception as exc:
            logger.warning("Could not initialize workspace on login: %s", exc)

        from qwenpaw.config.context import set_current_workspace_dir

        user_dir = get_user_working_dir(user_id)
        set_current_workspace_dir(user_dir)

        resp_data = {"token": token, "user_id": user_id, **stripped}
        return LoginResponse(**resp_data)
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error("[multi-user] Login error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Login error: {e}")


@router.post("/init-workspace/")
@router.post("/init-workspace")
async def init_workspace(request: Request, req: InitWorkspaceRequest = None):
    """Initialize a user workspace without a password (integration mode)."""
    from auth_extension import (
        is_auth_enabled,
        ensure_user_workspace,
        get_user_working_dir,
        _load_auth_data,
        build_user_id as _build,
    )

    if is_auth_enabled():
        raise HTTPException(
            status_code=403,
            detail="This endpoint is only available in integration mode.",
        )

    from token_parser import parse_request_user_fields

    body_fields = None
    if req is not None:
        explicit = {}
        for field in USER_FIELDS:
            val = getattr(req, field, None)
            explicit[field] = (val or "").strip()
        if all(explicit.values()):
            body_fields = explicit

    if body_fields is not None:
        stripped = body_fields
    else:
        parsed = parse_request_user_fields(request.headers.get("Authorization", ""))
        if parsed is None:
            raise HTTPException(
                status_code=401,
                detail="Cannot resolve user identity from token.",
            )
        stripped = {k: v.strip() for k, v in parsed.items()}

    existing_user_id = _build(**stripped)
    auth_data = _load_auth_data()
    already_existed = existing_user_id in auth_data.get("users", {})

    user_id = ensure_user_workspace(**stripped)

    try:
        from migration_extension import ensure_user_default_agent_exists

        ensure_user_default_agent_exists(user_id)
    except Exception as exc:
        logger.warning("Could not initialize agent workspace for '%s': %s", user_id, exc)

    from qwenpaw.config.context import set_current_workspace_dir

    set_current_workspace_dir(get_user_working_dir(user_id))

    resp_data = {"user_id": user_id, "initialized": not already_existed, **stripped}
    return InitWorkspaceResponse(**resp_data)


@router.get("/status/")
@router.get("/status")
async def auth_status():
    """Check authentication and multi-user status."""
    from auth_extension import is_auth_enabled as _ae, is_multi_user_enabled as _mu, has_registered_users as _hu

    return AuthStatusResponse(
        enabled=_ae(),
        has_users=_hu(),
        multi_user=_mu(),
        user_fields=list(USER_FIELDS),
        user_field_labels_zh=dict(USER_FIELD_LABELS_ZH),
        user_field_labels_en=dict(USER_FIELD_LABELS_EN),
        user_field_labels_ja=dict(USER_FIELD_LABELS_JA),
        user_field_labels_ru=dict(USER_FIELD_LABELS_RU),
    )


@router.get("/resolve-user/")
@router.get("/resolve-user")
async def resolve_user(request: Request):
    """Resolve user identity from upstream Authorization token."""
    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else ""

    if not token:
        return {"user_id": ""}

    try:
        from token_parser import parse_token_to_user_id, get_token_parser

        user_id = parse_token_to_user_id(token)
        if user_id:
            from auth_extension import parse_user_id as _parse

            fields = _parse(user_id)
            return {"user_id": user_id, **fields}
    except Exception:
        pass

    return {"user_id": ""}


@router.get("/verify/")
@router.get("/verify")
async def verify(request: Request):
    """Verify that the caller's Bearer token is still valid."""
    from auth_extension import is_auth_enabled as _ae, verify_token as _vt, get_user_info as _gui

    if not _ae():
        return {"valid": True, "user_id": ""}

    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail="No token provided")

    user_id = _vt(token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user_info = _gui(user_id)
    if user_info:
        return {"valid": True, **user_info}
    return {"valid": True, "user_id": user_id}


# ---------------------------------------------------------------------------
# Protected endpoints (require valid Bearer token)
# ---------------------------------------------------------------------------


def _require_auth(request: Request) -> str:
    """Extract and verify the Bearer token; return user_id or raise 401."""
    from auth_extension import verify_token as _vt

    auth_header = request.headers.get("Authorization", "")
    caller_token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
    if not caller_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_id = _vt(caller_token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user_id


@router.get("/users")
async def get_users(request: Request):
    """List all registered users."""
    from auth_extension import is_auth_enabled as _ae, list_users as _lu

    if not _ae():
        return {"users": []}

    _require_auth(request)
    return {"users": _lu()}


@router.post("/update-profile")
async def update_profile(req: UpdateProfileRequest, request: Request):
    """Update password for the authenticated user."""
    from auth_extension import (
        is_auth_enabled as _ae,
        has_registered_users as _hu,
        update_credentials as _uc,
        get_user_info as _gui,
    )

    if not _ae():
        raise HTTPException(status_code=403, detail="Authentication not enabled")

    if not _hu():
        raise HTTPException(status_code=403, detail="No user registered")

    caller_user_id = _require_auth(request)

    if not req.new_password or not req.new_password.strip():
        raise HTTPException(status_code=400, detail="New password required")

    token = _uc(user_id=caller_user_id, current_password=req.current_password, new_password=req.new_password.strip())
    if token is None:
        raise HTTPException(status_code=401, detail="Current password incorrect")

    user_info = _gui(caller_user_id)
    return {"token": token, **(user_info or {"user_id": caller_user_id})}


@router.delete("/users/{user_id:path}")
async def remove_user(user_id: str, request: Request):
    """Delete a user account."""
    from auth_extension import is_auth_enabled as _ae, delete_user as _du

    if not _ae():
        raise HTTPException(status_code=403, detail="Authentication not enabled")

    admin_user_id = _require_auth(request)

    success = _du(user_id=user_id, admin_user_id=admin_user_id)
    if not success:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete '{user_id}' (not found or last remaining user)",
        )
    return {"success": True, "deleted_user_id": user_id}


# ---------------------------------------------------------------------------
# Token revocation endpoints (replacing upstream routes removed by _app.py)
# ---------------------------------------------------------------------------


class RevokeTokenRequest(BaseModel):
    """Request body for revoking a single token."""
    token: str


@router.post("/revoke-token")
async def revoke_token_endpoint(req: RevokeTokenRequest) -> dict:
    """Revoke a single token by adding its jti to the blacklist.

    This replaces the upstream ``/api/auth/revoke-token`` endpoint that
    was removed during multi-user route override in ``_app.py``.
    """
    from auth_extension import revoke_token as _revoke_token

    success = _revoke_token(req.token)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to revoke token")
    return {"success": True}


@router.post("/revoke-all-tokens")
async def revoke_all_tokens_endpoint() -> dict:
    """Revoke all existing tokens by rotating the JWT secret.

    This replaces the upstream ``/api/auth/revoke-all-tokens`` endpoint
    that was removed during multi-user route override in ``_app.py``.
    """
    from auth_extension import revoke_all_tokens as _revoke_all_tokens

    success = _revoke_all_tokens()
    if not success:
        raise HTTPException(status_code=500, detail="Failed to revoke all tokens")
    return {"success": True}


def get_auth_router() -> APIRouter:
    """Return the multi-user auth router for inclusion in the app."""
    return router
