# -*- coding: utf-8 -*-
"""Router extension: additional multi-tenant auth API endpoints.

These endpoints are registered via ``app.include_router()`` during plugin
activation.  They coexist alongside the upstream's own routers under
different path prefixes.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["multi-tenant-auth"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    sysId: str
    branchId: str
    vorgCode: str
    sapId: str
    positionId: str
    password: str


class LoginResponse(BaseModel):
    token: str
    tenant_id: str
    sysId: str
    branchId: str
    vorgCode: str
    sapId: str
    positionId: str


class InitWorkspaceRequest(BaseModel):
    sysId: str | None = None
    branchId: str | None = None
    vorgCode: str | None = None
    sapId: str | None = None
    positionId: str | None = None


class InitWorkspaceResponse(BaseModel):
    tenant_id: str
    sysId: str
    branchId: str
    vorgCode: str
    sapId: str
    positionId: str
    initialized: bool


class AuthStatusResponse(BaseModel):
    enabled: bool
    has_users: bool
    multi_tenant: bool


class UpdateProfileRequest(BaseModel):
    current_password: str
    new_password: str | None = None


class DeleteUserRequest(BaseModel):
    tenant_id: str


# ---------------------------------------------------------------------------
# Public endpoints (no auth required)
# ---------------------------------------------------------------------------


@router.post("/login")
async def login(req: LoginRequest):
    """Authenticate with tenant fields and password.

    Auto-registers if user does not exist.
    """
    from .auth_extension import (
        authenticate,
        build_tenant_id,
        is_auth_enabled,
        register_user,
        get_tenant_working_dir,
    )

    if not is_auth_enabled():
        return LoginResponse(
            token="", tenant_id="", sysId="", branchId="",
            vorgCode="", sapId="", positionId="",
        )

    stripped = {
        "sysId": req.sysId.strip(),
        "branchId": req.branchId.strip(),
        "vorgCode": req.vorgCode.strip(),
        "sapId": req.sapId.strip(),
        "positionId": req.positionId.strip(),
    }

    token = authenticate(
        stripped["sysId"], stripped["branchId"],
        stripped["vorgCode"], stripped["sapId"],
        stripped["positionId"], req.password,
    )
    if token is None:
        token = register_user(
            stripped["sysId"], stripped["branchId"],
            stripped["vorgCode"], stripped["sapId"],
            stripped["positionId"], req.password,
        )
        if token is None:
            raise HTTPException(status_code=401, detail="Invalid credentials")

    tenant_id = build_tenant_id(**stripped)

    # Initialize tenant workspace on login
    try:
        from .migration_extension import ensure_tenant_default_agent_exists

        ensure_tenant_default_agent_exists(tenant_id)
    except Exception as exc:
        logger.warning("Could not initialize workspace on login: %s", exc)

    from qwenpaw.config.context import set_current_workspace_dir

    tenant_dir = get_tenant_working_dir(tenant_id)
    set_current_workspace_dir(tenant_dir)

    return LoginResponse(
        token=token, tenant_id=tenant_id,
        sysId=stripped["sysId"], branchId=stripped["branchId"],
        vorgCode=stripped["vorgCode"], sapId=stripped["sapId"],
        positionId=stripped["positionId"],
    )


@router.post("/init-workspace")
async def init_workspace(request: Request, req: InitWorkspaceRequest = None):
    """Initialize a tenant workspace without a password (integration mode)."""
    from .auth_extension import (
        is_auth_enabled,
        ensure_tenant_workspace,
        get_tenant_working_dir,
        _load_auth_data,
        build_tenant_id as _build,
    )

    if is_auth_enabled():
        raise HTTPException(
            status_code=403,
            detail="This endpoint is only available in integration mode.",
        )

    from .token_parser import parse_request_tenant_fields

    body_fields = None
    if req is not None:
        explicit = {
            "sysId": (req.sysId or "").strip(),
            "branchId": (req.branchId or "").strip(),
            "vorgCode": (req.vorgCode or "").strip(),
            "sapId": (req.sapId or "").strip(),
            "positionId": (req.positionId or "").strip(),
        }
        if all(explicit.values()):
            body_fields = explicit

    if body_fields is not None:
        stripped = body_fields
    else:
        parsed = parse_request_tenant_fields(request.headers.get("Authorization", ""))
        if parsed is None:
            raise HTTPException(
                status_code=401,
                detail="Cannot resolve tenant identity from token.",
            )
        stripped = {k: v.strip() for k, v in parsed.items()}

    existing_tenant_id = _build(**stripped)
    auth_data = _load_auth_data()
    already_existed = existing_tenant_id in auth_data.get("users", {})

    tenant_id = ensure_tenant_workspace(**stripped)

    try:
        from .migration_extension import ensure_tenant_default_agent_exists

        ensure_tenant_default_agent_exists(tenant_id)
    except Exception as exc:
        logger.warning("Could not initialize agent workspace for '%s': %s", tenant_id, exc)

    from qwenpaw.config.context import set_current_workspace_dir

    set_current_workspace_dir(get_tenant_working_dir(tenant_id))

    return InitWorkspaceResponse(
        tenant_id=tenant_id, initialized=not already_existed, **stripped,
    )


@router.get("/status")
async def auth_status():
    """Check authentication and multi-tenant status."""
    from .auth_extension import is_auth_enabled as _ae, is_multi_tenant_enabled as _mt, has_registered_users as _hu

    return AuthStatusResponse(enabled=_ae(), has_users=_hu(), multi_tenant=_mt())


@router.get("/resolve-tenant")
async def resolve_tenant(request: Request):
    """Resolve tenant identity from upstream Authorization token."""
    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else ""

    if not token:
        return {"tenant_id": ""}

    try:
        from .token_parser import parse_token_to_tenant_id, get_token_parser

        tenant_id = parse_token_to_tenant_id(token)
        if tenant_id:
            from .auth_extension import parse_tenant_id as _parse

            fields = _parse(tenant_id)
            return {"tenant_id": tenant_id, **fields}
    except Exception:
        pass

    return {"tenant_id": ""}


@router.get("/verify")
async def verify(request: Request):
    """Verify that the caller's Bearer token is still valid."""
    from .auth_extension import is_auth_enabled as _ae, verify_token as _vt, get_user_info as _gui

    if not _ae():
        return {"valid": True, "tenant_id": ""}

    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail="No token provided")

    tenant_id = _vt(token)
    if tenant_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user_info = _gui(tenant_id)
    if user_info:
        return {"valid": True, **user_info}
    return {"valid": True, "tenant_id": tenant_id}


# ---------------------------------------------------------------------------
# Protected endpoints (require valid Bearer token)
# ---------------------------------------------------------------------------


def _require_auth(request: Request) -> str:
    """Extract and verify the Bearer token; return tenant_id or raise 401."""
    from .auth_extension import verify_token as _vt

    auth_header = request.headers.get("Authorization", "")
    caller_token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
    if not caller_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    tenant_id = _vt(caller_token)
    if tenant_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return tenant_id


@router.get("/users")
async def get_users(request: Request):
    """List all registered users."""
    from .auth_extension import is_auth_enabled as _ae, list_users as _lu

    if not _ae():
        return {"users": []}

    _require_auth(request)
    return {"users": _lu()}


@router.post("/update-profile")
async def update_profile(req: UpdateProfileRequest, request: Request):
    """Update password for the authenticated tenant."""
    from .auth_extension import (
        is_auth_enabled as _ae,
        has_registered_users as _hu,
        update_credentials as _uc,
        get_user_info as _gui,
    )

    if not _ae():
        raise HTTPException(status_code=403, detail="Authentication not enabled")

    if not _hu():
        raise HTTPException(status_code=403, detail="No user registered")

    caller_tenant_id = _require_auth(request)

    if not req.new_password or not req.new_password.strip():
        raise HTTPException(status_code=400, detail="New password required")

    token = _uc(tenant_id=caller_tenant_id, current_password=req.current_password, new_password=req.new_password.strip())
    if token is None:
        raise HTTPException(status_code=401, detail="Current password incorrect")

    user_info = _gui(caller_tenant_id)
    return {"token": token, **(user_info or {"tenant_id": caller_tenant_id})}


@router.delete("/users/{tenant_id:path}")
async def remove_user(tenant_id: str, request: Request):
    """Delete a tenant account."""
    from .auth_extension import is_auth_enabled as _ae, delete_user as _du

    if not _ae():
        raise HTTPException(status_code=403, detail="Authentication not enabled")

    admin_tenant_id = _require_auth(request)

    success = _du(tenant_id=tenant_id, admin_tenant_id=admin_tenant_id)
    if not success:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete '{tenant_id}' (not found or last remaining user)",
        )
    return {"success": True, "deleted_tenant_id": tenant_id}


def get_auth_router() -> APIRouter:
    """Return the multi-tenant auth router for inclusion in the app."""
    return router
