# -*- coding: utf-8 -*-
"""Auth extension: multi-user authentication system.

This module replaces the single-user authentication in QwenPaw's
``qwenpaw.app.auth`` with a full multi-tenant implementation ported from
CoPaw.

Key differences from the upstream single-user auth:
- Supports N users (not just one), each identified by a five-tuple:
  ``(sysId, branchId, vorgCode, sapId, positionId)``
- Each user gets an isolated working directory under ``WORKING_DIR/tenants/``
- Two modes:
  - **Standalone**: built-in HMAC token validation (``QWENPAW_AUTH_ENABLED=true``)
  - **Integration**: trusts upstream gateway tokens (``QWENPAW_AUTH_ENABLED=false``)

.. note::

    This is a scaffold with all function signatures matching CoPaw's
    implementation.  Fill in the bodies during Phase 3 of the migration plan.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from qwenpaw.constant import SECRET_DIR, WORKING_DIR

from .constants import (
    ENV_AUTH_ENABLED,
    PUBLIC_PATHS,
    PUBLIC_PREFIXES,
    TENANT_FIELDS,
    TOKEN_EXPIRY_SECONDS,
    TENANT_FIELD_LABELS_ZH,
    TENANT_FIELD_LABELS_EN,
    DEFAULT_TENANT_ID,
)

logger = logging.getLogger(__name__)

AUTH_FILE = SECRET_DIR / "auth.json"


# ===================================================================
# Tenant ID helpers
# ===================================================================


def build_tenant_id(
    sysId: str, branchId: str, vorgCode: str, sapId: str, positionId: str
) -> str:
    """Build a composite tenant ID from the five identifying fields."""
    parts = [sysId.strip(), branchId.strip(), vorgCode.strip(), sapId.strip(), positionId.strip()]
    if not all(parts):
        raise ValueError("All tenant fields must be non-empty")
    return "/".join(parts)


def parse_tenant_id(tenant_id: str) -> Dict[str, str]:
    """Parse a composite tenant ID back into its five fields."""
    parts = tenant_id.split("/")
    if len(parts) != 5 or not all(parts):
        raise ValueError(f"Invalid tenant_id format: {tenant_id}")
    return dict(zip(TENANT_FIELDS, parts))


def _sanitize_path_segment(segment: str) -> str:
    """Sanitize a path segment to prevent path traversal."""
    return "".join(c for c in segment if c.isalnum() or c in ("-", "_", "."))


# ===================================================================
# Password hashing (standalone mode only)
# ===================================================================


def _hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    """Hash password with salt using SHA-256."""
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return h, salt


def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    """Verify password against stored hash."""
    h, _ = _hash_password(password, salt)
    return hmac.compare_digest(h, stored_hash)


# ===================================================================
# Token generation / verification (HMAC-SHA256, no PyJWT needed)
# ===================================================================


def _get_jwt_secret() -> str:
    """Return the signing secret, creating one if absent."""
    data = _load_auth_data()
    secret = data.get("jwt_secret", "")
    if not secret:
        secret = secrets.token_hex(32)
        data["jwt_secret"] = secret
        _save_auth_data(data)
    return secret


def create_token(tenant_id: str) -> str:
    """Create an HMAC-signed token: ``base64(payload).signature``."""
    import base64

    secret = _get_jwt_secret()
    payload = json.dumps(
        {"sub": tenant_id, "exp": int(time.time()) + TOKEN_EXPIRY_SECONDS, "iat": int(time.time())}
    )
    payload_b64 = base64.urlsafe_b64encode(payload.encode()).decode()
    sig = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def verify_token(token: str) -> Optional[str]:
    """Verify token, return tenant_id if valid, else None."""
    import base64

    try:
        parts = token.split(".", 1)
        if len(parts) != 2:
            return None
        payload_b64, sig = parts
        secret = _get_jwt_secret()
        expected_sig = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        if payload.get("exp", 0) < time.time():
            return None
        return payload.get("sub")
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        logger.debug("Token verification failed: %s", exc)
        return None


# ===================================================================
# Auth data persistence
# ===================================================================


def _load_auth_data() -> dict:
    """Load auth.json from SECRET_DIR."""
    if AUTH_FILE.is_file():
        try:
            with open(AUTH_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to load auth file %s: %s", AUTH_FILE, exc)
            return {"_auth_load_error": True}
    return {}


def _save_auth_data(data: dict) -> None:
    """Save auth.json with restrictive permissions."""
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(AUTH_FILE.parent, 0o700)
    except OSError:
        pass
    with open(AUTH_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    try:
        os.chmod(AUTH_FILE, 0o600)
    except OSError:
        pass


# ===================================================================
# Auth status helpers
# ===================================================================


def is_auth_enabled() -> bool:
    """Check whether built-in authentication is enabled."""
    env_flag = os.environ.get(ENV_AUTH_ENABLED, "false").strip().lower()
    return env_flag in ("true", "1", "yes")


def is_multi_tenant_enabled() -> bool:
    """Check whether multi-tenant mode is enabled."""
    from .constants import ENV_MULTI_TENANT_ENABLED

    env_flag = os.environ.get(ENV_MULTI_TENANT_ENABLED, "true").strip().lower()
    if env_flag in ("true", "1", "yes"):
        return True
    if env_flag in ("false", "0", "no"):
        return False
    # Not explicitly set
    if is_auth_enabled():
        return has_registered_users()
    else:
        return True  # Integration mode always routes by tenant


def has_registered_users() -> bool:
    """Return True if at least one user has been registered."""
    data = _load_auth_data()
    users = data.get("users", {})
    if not users and data.get("user"):
        return True
    return bool(users)


def list_users() -> List[str]:
    """Return list of registered tenant IDs."""
    data = _load_auth_data()
    users = data.get("users", {})
    if not users and data.get("user"):
        legacy_username = data["user"].get("username", "")
        return [legacy_username] if legacy_username else []
    return list(users.keys())


# ===================================================================
# Tenant directory helpers
# ===================================================================


def get_tenant_working_dir(tenant_id: str) -> Path:
    """Return working directory for a specific tenant."""
    try:
        fields = parse_tenant_id(tenant_id)
    except ValueError:
        fields = {"default": _sanitize_path_segment(tenant_id or "default")}

    if set(fields.keys()) == set(TENANT_FIELDS):
        tenant_dir = (
            WORKING_DIR
            / "tenants"
            / _sanitize_path_segment(fields["sysId"])
            / _sanitize_path_segment(fields["branchId"])
            / _sanitize_path_segment(fields["vorgCode"])
            / _sanitize_path_segment(fields["sapId"])
            / _sanitize_path_segment(fields["positionId"])
        )
    else:
        tenant_dir = WORKING_DIR / "tenants" / _sanitize_path_segment(tenant_id)

    tenant_dir.mkdir(parents=True, exist_ok=True)
    return tenant_dir


def get_tenant_secret_dir(tenant_id: str) -> Path:
    """Return secret directory for a specific tenant."""
    try:
        fields = parse_tenant_id(tenant_id)
    except ValueError:
        fields = None

    if fields and set(fields.keys()) == set(TENANT_FIELDS):
        secret_dir = (
            SECRET_DIR
            / "tenants"
            / _sanitize_path_segment(fields["sysId"])
            / _sanitize_path_segment(fields["branchId"])
            / _sanitize_path_segment(fields["vorgCode"])
            / _sanitize_path_segment(fields["sapId"])
            / _sanitize_path_segment(fields["positionId"])
        )
    else:
        secret_dir = SECRET_DIR / "tenants" / _sanitize_path_segment(tenant_id or "default")

    secret_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(secret_dir, 0o700)
    except OSError:
        pass
    return secret_dir


# ===================================================================
# User registration & authentication
# ===================================================================


def register_user(sysId, branchId, vorgCode, sapId, positionId, password) -> Optional[str]:
    """Register a new user account. Returns token on success."""
    tenant_id = build_tenant_id(sysId, branchId, vorgCode, sapId, positionId)
    data = _load_auth_data()
    users = data.setdefault("users", {})

    if tenant_id in users:
        return None

    pw_hash, salt = _hash_password(password)
    users[tenant_id] = {
        "tenant_id": tenant_id,
        "sysId": sysId.strip(),
        "branchId": branchId.strip(),
        "vorgCode": vorgCode.strip(),
        "sapId": sapId.strip(),
        "positionId": positionId.strip(),
        "password_hash": pw_hash,
        "password_salt": salt,
    }

    if not data.get("jwt_secret"):
        data["jwt_secret"] = secrets.token_hex(32)

    _save_auth_data(data)

    # Ensure tenant config.json exists so get_agent() can lazy-load
    # a tenant-specific Workspace instead of falling back to default.
    from .config_extension import ensure_tenant_config_exists
    ensure_tenant_config_exists(tenant_id)

    tenant_dir = get_tenant_working_dir(tenant_id)
    logger.info("User registered: tenant_id='%s', dir: %s", tenant_id, tenant_dir)
    return create_token(tenant_id)


def ensure_tenant_workspace(sysId, branchId, vorgCode, sapId, positionId) -> str:
    """Initialize tenant workspace without password (integration mode)."""
    tenant_id = build_tenant_id(sysId, branchId, vorgCode, sapId, positionId)
    data = _load_auth_data()
    users = data.setdefault("users", {})

    if tenant_id not in users:
        users[tenant_id] = {
            "tenant_id": tenant_id,
            "sysId": sysId.strip(),
            "branchId": branchId.strip(),
            "vorgCode": vorgCode.strip(),
            "sapId": sapId.strip(),
            "positionId": positionId.strip(),
        }
        if not data.get("jwt_secret"):
            data["jwt_secret"] = secrets.token_hex(32)
        _save_auth_data(data)

        # Ensure tenant config.json exists so get_agent() can lazy-load
        from .config_extension import ensure_tenant_config_exists
        ensure_tenant_config_exists(tenant_id)

        logger.info("Tenant workspace created (no-password): '%s'", tenant_id)
    else:
        logger.debug("Tenant workspace already exists: '%s'", tenant_id)

    return tenant_id


def authenticate(sysId, branchId, vorgCode, sapId, positionId, password) -> Optional[str]:
    """Authenticate with five fields + password. Returns token if valid."""
    tenant_id = build_tenant_id(sysId, branchId, vorgCode, sapId, positionId)
    data = _load_auth_data()
    users = data.get("users", {})

    user = users.get(tenant_id)
    if not user:
        return None

    stored_hash = user.get("password_hash", "")
    stored_salt = user.get("password_salt", "")
    if stored_hash and stored_salt and verify_password(password, stored_hash, stored_salt):
        # Ensure tenant config exists (repair legacy registrations that
        # were created before config.json auto-generation was added).
        from .config_extension import ensure_tenant_config_exists
        ensure_tenant_config_exists(tenant_id)
        return create_token(tenant_id)
    return None


def get_user_info(tenant_id: str) -> Optional[Dict[str, str]]:
    """Get tenant user info by tenant_id."""
    data = _load_auth_data()
    users = data.get("users", {})
    user = users.get(tenant_id)
    if not user:
        return None

    if "sysId" in user:
        return {
            "tenant_id": tenant_id,
            "sysId": user.get("sysId", ""),
            "branchId": user.get("branchId", ""),
            "vorgCode": user.get("vorgCode", ""),
            "sapId": user.get("sapId", ""),
            "positionId": user.get("positionId", ""),
        }
    else:
        return {"tenant_id": tenant_id, "username": user.get("username", "")}


def update_credentials(tenant_id, current_password, new_password=None) -> Optional[str]:
    """Update a tenant's password."""
    data = _load_auth_data()
    users = data.get("users", {})
    user = users.get(tenant_id)
    if not user:
        return None

    stored_hash = user.get("password_hash", "")
    stored_salt = user.get("password_salt", "")
    if not verify_password(current_password, stored_hash, stored_salt):
        return None

    if new_password:
        pw_hash, salt = _hash_password(new_password)
        users[tenant_id]["password_hash"] = pw_hash
        users[tenant_id]["password_salt"] = salt
        data["jwt_secret"] = secrets.token_hex(32)

    data["users"] = users
    _save_auth_data(data)
    return create_token(tenant_id)


def delete_user(tenant_id: str, admin_tenant_id: str) -> bool:
    """Delete a tenant account (admin only)."""
    data = _load_auth_data()
    users = data.get("users", {})

    if tenant_id not in users:
        return False

    if len(users) <= 1:
        logger.warning("Cannot delete last user '%s'", tenant_id)
        return False

    del users[tenant_id]
    data["users"] = users
    _save_auth_data(data)
    return True


# ===================================================================
# Auto-register from environment variables
# ===================================================================


def auto_register_from_env() -> None:
    """Auto-register admin user from environment variables."""
    if not is_auth_enabled():
        return

    from .constants import (
        ENV_AUTH_SYSID,
        ENV_AUTH_BRANCHID,
        ENV_AUTH_VORGCODE,
        ENV_AUTH_SAPID,
        ENV_AUTH_POSITIONID,
        ENV_AUTH_PASSWORD,
        ENV_AUTH_USERNAME,
    )

    env_fields = {
        "sysId": os.environ.get(ENV_AUTH_SYSID, "").strip(),
        "branchId": os.environ.get(ENV_AUTH_BRANCHID, "").strip(),
        "vorgCode": os.environ.get(ENV_AUTH_VORGCODE, "").strip(),
        "sapId": os.environ.get(ENV_AUTH_SAPID, "").strip(),
        "positionId": os.environ.get(ENV_AUTH_POSITIONID, "").strip(),
    }
    env_password = os.environ.get(ENV_AUTH_PASSWORD, "").strip()

    if all(env_fields.values()) and env_password:
        tenant_id = build_tenant_id(**env_fields)
        if tenant_id not in list_users():
            token = register_user(**env_fields, password=env_password)
            if token:
                logger.info("Auto-registered tenant from env vars: '%s'", tenant_id)
        return

    # Legacy env var support
    username = os.environ.get(ENV_AUTH_USERNAME, "").strip()
    password = env_password
    if username or password:
        if not username or not password:
            return
        # Legacy registration handled separately
        pass


# ===================================================================
# FastAPI Middleware
# ===================================================================


class AuthMiddleware(BaseHTTPMiddleware):
    """Multi-tenant auth middleware.

    In standalone mode (QWENPAW_AUTH_ENABLED=true):
      Validates HMAC tokens and routes requests to tenant workspaces.

    In integration mode (QWENPAW_AUTH_ENABLED=false):
      Skips token validation; parses tenant identity from upstream token
      via the pluggable TokenParser and routes accordingly.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request with multi-tenant routing."""
        if self._should_skip_auth(request):
            # Still resolve tenant when multi-tenant is on
            if is_multi_tenant_enabled():
                token = self._extract_token(request)
                if token:
                    from .token_parser import parse_token_to_tenant_id

                    tenant_id = parse_token_to_tenant_id(token) or "default"
                    request.state.user = tenant_id
                    request.state.tenant_id = tenant_id

                    from .tenant_context import set_current_tenant_id

                    set_current_tenant_id(tenant_id)

                    from qwenpaw.config.context import set_current_workspace_dir

                    tenant_dir = get_tenant_working_dir(tenant_id)
                    set_current_workspace_dir(tenant_dir)

                # Propagate X-Agent-Id to ContextVar so that
                # token_usage and agent_stats can resolve per-agent paths.
                agent_id = request.headers.get("X-Agent-Id")
                if agent_id:
                    from qwenpaw.app.agent_context import set_current_agent_id
                    set_current_agent_id(agent_id)

            return await call_next(request)

        # --- Standalone mode: validate HMAC token ---
        token = self._extract_token(request)
        if not token:
            return Response(content=json.dumps({"detail": "Not authenticated"}), status_code=401, media_type="application/json")

        tenant_id = verify_token(token)
        if tenant_id is None:
            return Response(
                content=json.dumps({"detail": "Invalid or expired token"}),
                status_code=401,
                media_type="application/json",
            )

        request.state.user = tenant_id
        request.state.tenant_id = tenant_id

        from .tenant_context import set_current_tenant_id

        set_current_tenant_id(tenant_id)

        from qwenpaw.config.context import set_current_workspace_dir

        tenant_dir = get_tenant_working_dir(tenant_id)
        set_current_workspace_dir(tenant_dir)

        # Propagate X-Agent-Id to ContextVar so that
        # token_usage and agent_stats can resolve per-agent paths.
        agent_id = request.headers.get("X-Agent-Id")
        if agent_id:
            from qwenpaw.app.agent_context import set_current_agent_id
            set_current_agent_id(agent_id)

        logger.debug("Switched to tenant '%s' dir: %s", tenant_id, tenant_dir)
        return await call_next(request)

    @staticmethod
    def _should_skip_auth(request: Request) -> bool:
        """Return True when HMAC validation should be skipped."""
        if not is_auth_enabled() or not has_registered_users():
            return True

        path = request.url.path
        if request.method == "OPTIONS":
            return True
        if path in PUBLIC_PATHS or any(path.startswith(p) for p in PUBLIC_PREFIXES):
            return True
        if not path.startswith("/api/"):
            return True
        return False

    @staticmethod
    def _extract_token(request: Request) -> Optional[str]:
        """Extract Bearer token from header or query param."""
        from .token_parser import extract_bearer_token

        token = extract_bearer_token(request.headers.get("Authorization", ""))
        if token:
            return token
        if "upgrade" in request.headers.get("connection", "").lower():
            return request.query_params.get("token")
        token = request.query_params.get("token")
        if token:
            return token
        return None


# ===================================================================
# Patching entry point
# ===================================================================


def patch_auth_module() -> None:
    """Replace ``qwenpaw.app.auth`` with our multi-tenant version.

    This makes ``from qwenpaw.app.auth import ...`` everywhere resolve to
    the multi-tenant implementations transparently.
    """
    import qwenpaw.app.auth as upstream_auth

    # Export our key symbols into the upstream namespace
    # so existing imports like `from ..app.auth import AuthMiddleware` still work
    upstream_auth.AuthMiddleware = AuthMiddleware
    upstream_auth.verify_token = verify_token
    upstream_auth.create_token = create_token
    upstream_auth.is_auth_enabled = is_auth_enabled
    upstream_auth.is_multi_tenant_enabled = is_multi_tenant_enabled
    upstream_auth.has_registered_users = has_registered_users
    upstream_auth.list_users = list_users
    upstream_auth.register_user = register_user
    upstream_auth.authenticate = authenticate
    upstream_auth.get_user_info = get_user_info
    upstream_auth.update_credentials = update_credentials
    upstream_auth.delete_user = delete_user
    upstream_auth.ensure_tenant_workspace = ensure_tenant_workspace
    upstream_auth.get_tenant_working_dir = get_tenant_working_dir
    upstream_auth.get_tenant_secret_dir = get_tenant_secret_dir
    upstream_auth.build_tenant_id = build_tenant_id
    upstream_auth.parse_tenant_id = parse_tenant_id
    upstream_auth.auto_register_from_env = auto_register_from_env
    upstream_auth.TENANT_FIELDS = TENANT_FIELDS
    upstream_auth.PUBLIC_PATHS = PUBLIC_PATHS
    upstream_auth.PUBLIC_PREFIXES = PUBLIC_PREFIXES

    # Run auto-registration if configured
    auto_register_from_env()

    logger.debug("[multi-tenant/auth] Patched qwenpaw.app.auth module")
