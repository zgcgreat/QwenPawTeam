# -*- coding: utf-8 -*-
"""Auth extension: multi-user authentication system.

This module replaces the single-user authentication in QwenPaw's
``qwenpaw.app.auth`` with a full multi-user implementation ported from
CoPaw.

Key differences from the upstream single-user auth:
- Supports N users (not just one), each identified by a configurable set
  of fields (default: ``username``; configurable via ``QWENPAW_USER_FIELDS``).
- Each user gets an isolated working directory under ``WORKING_DIR/users/``
- Two modes:
  - **Standalone**: built-in HMAC token validation (``QWENPAW_AUTH_ENABLED=true``)
  - **Integration**: trusts upstream gateway tokens (``QWENPAW_AUTH_ENABLED=false``)

The user field set is configured via ``QWENPAW_USER_FIELDS`` env var.
All functions accept ``**fields`` (keyword dict) instead of positional args.
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
    USER_FIELDS,
    USER_ID_SEPARATOR,
    TOKEN_EXPIRY_SECONDS,
    USER_FIELD_LABELS_ZH,
    USER_FIELD_LABELS_EN,
    DEFAULT_USER_ID,
    get_env_var_for_field,
)

logger = logging.getLogger(__name__)

AUTH_FILE = SECRET_DIR / "auth.json"


# ===================================================================
# User ID helpers
# ===================================================================


def build_user_id(**fields: str) -> str:
    """Build a composite user ID from the configured user fields.

    Args:
        **fields: Keyword arguments matching ``USER_FIELDS``.
            Example: ``build_user_id(username="admin")``
            or ``build_user_id(orgId="A", deptId="B", userId="C")``

    Returns:
        Slash-separated composite ID string.

    Raises:
        ValueError: If any required field is missing or empty.
    """
    parts = []
    for field in USER_FIELDS:
        value = fields.get(field, "").strip()
        if not value:
            raise ValueError(
                f"Missing or empty user field '{field}'. "
                f"Required fields: {USER_FIELDS}"
            )
        parts.append(value)
    return USER_ID_SEPARATOR.join(parts)


def parse_user_id(user_id: str) -> Dict[str, str]:
    """Parse a composite user ID back into its component fields.

    Args:
        user_id: Slash-separated composite user ID.

    Returns:
        Dict mapping field names to values.

    Raises:
        ValueError: If the number of segments doesn't match USER_FIELDS.
    """
    parts = user_id.split(USER_ID_SEPARATOR)
    expected = len(USER_FIELDS)
    if len(parts) != expected or not all(parts):
        raise ValueError(
            f"Invalid user_id format: {user_id!r} "
            f"(expected {expected} segments, got {len(parts)})"
        )
    return dict(zip(USER_FIELDS, parts))


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


def create_token(user_id: str) -> str:
    """Create an HMAC-signed token: ``base64(payload).signature``."""
    import base64

    secret = _get_jwt_secret()
    payload = json.dumps(
        {"sub": user_id, "exp": int(time.time()) + TOKEN_EXPIRY_SECONDS, "iat": int(time.time())}
    )
    payload_b64 = base64.urlsafe_b64encode(payload.encode()).decode()
    sig = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def verify_token(token: str) -> Optional[str]:
    """Verify token, return user_id if valid, else None."""
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


def is_multi_user_enabled() -> bool:
    """Check whether multi-user mode is enabled."""
    from .constants import ENV_MULTI_USER_ENABLED

    env_flag = os.environ.get(ENV_MULTI_USER_ENABLED, "true").strip().lower()
    if env_flag in ("true", "1", "yes"):
        return True
    if env_flag in ("false", "0", "no"):
        return False
    # Not explicitly set
    if is_auth_enabled():
        return has_registered_users()
    else:
        return True  # Integration mode always routes by user


def has_registered_users() -> bool:
    """Return True if at least one user has been registered."""
    data = _load_auth_data()
    users = data.get("users", {})
    if not users and data.get("user"):
        return True
    return bool(users)


def list_users() -> List[str]:
    """Return list of registered user IDs."""
    data = _load_auth_data()
    users = data.get("users", {})
    if not users and data.get("user"):
        legacy_username = data["user"].get("username", "")
        return [legacy_username] if legacy_username else []
    return list(users.keys())


# ===================================================================
# User directory helpers
# ===================================================================


def get_user_working_dir(user_id: str) -> Path:
    """Return working directory for a specific user.

    If *user_id* can be parsed as a composite matching the configured
    ``USER_FIELDS``, each field value becomes a subdirectory level.
    Otherwise, the raw user_id is used as a single directory name.
    """
    try:
        fields = parse_user_id(user_id)
    except ValueError:
        fields = {"default": _sanitize_path_segment(user_id or "default")}

    if set(fields.keys()) == set(USER_FIELDS):
        user_dir = WORKING_DIR / "users"
        for field in USER_FIELDS:
            user_dir = user_dir / _sanitize_path_segment(fields[field])
    else:
        user_dir = WORKING_DIR / "users" / _sanitize_path_segment(user_id)

    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def get_user_secret_dir(user_id: str) -> Path:
    """Return secret directory for a specific user."""
    try:
        fields = parse_user_id(user_id)
    except ValueError:
        fields = None

    if fields and set(fields.keys()) == set(USER_FIELDS):
        secret_dir = SECRET_DIR / "users"
        for field in USER_FIELDS:
            secret_dir = secret_dir / _sanitize_path_segment(fields[field])
    else:
        secret_dir = SECRET_DIR / "users" / _sanitize_path_segment(user_id or "default")

    secret_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(secret_dir, 0o700)
    except OSError:
        pass
    return secret_dir


# ===================================================================
# User registration & authentication
# ===================================================================


def register_user(password: str, **fields: str) -> Optional[str]:
    """Register a new user account. Returns token on success.

    Args:
        password: The user's password.
        **fields: Keyword arguments matching ``USER_FIELDS``.
            Example: ``register_user(password="x", username="admin")``

    Returns:
        Token string on success, ``None`` if user already exists.
    """
    user_id = build_user_id(**fields)
    data = _load_auth_data()
    users = data.setdefault("users", {})

    if user_id in users:
        return None

    pw_hash, salt = _hash_password(password)
    user_record = {
        "user_id": user_id,
        **{field: fields[field].strip() for field in USER_FIELDS},
        "password_hash": pw_hash,
        "password_salt": salt,
    }

    users[user_id] = user_record

    if not data.get("jwt_secret"):
        data["jwt_secret"] = secrets.token_hex(32)

    _save_auth_data(data)

    # Ensure user config.json exists so get_agent() can lazy-load
    # a user-specific Workspace instead of falling back to default.
    from .config_extension import ensure_user_config_exists
    ensure_user_config_exists(user_id)

    user_dir = get_user_working_dir(user_id)
    logger.info("User registered: user_id='%s', dir: %s", user_id, user_dir)
    return create_token(user_id)


def ensure_user_workspace(**fields: str) -> str:
    """Initialize user workspace without password (integration mode).

    Args:
        **fields: Keyword arguments matching ``USER_FIELDS``.

    Returns:
        The user_id string.
    """
    user_id = build_user_id(**fields)
    data = _load_auth_data()
    users = data.setdefault("users", {})

    if user_id not in users:
        users[user_id] = {
            "user_id": user_id,
            **{field: fields[field].strip() for field in USER_FIELDS},
        }
        if not data.get("jwt_secret"):
            data["jwt_secret"] = secrets.token_hex(32)
        _save_auth_data(data)

        # Ensure user config.json exists so get_agent() can lazy-load
        from .config_extension import ensure_user_config_exists
        ensure_user_config_exists(user_id)

        logger.info("User workspace created (no-password): '%s'", user_id)
    else:
        logger.debug("User workspace already exists: '%s'", user_id)

    return user_id


def authenticate(password: str, **fields: str) -> Optional[str]:
    """Authenticate with user fields + password. Returns token if valid.

    Args:
        password: The user's password.
        **fields: Keyword arguments matching ``USER_FIELDS``.

    Returns:
        Token string if authentication succeeds, ``None`` otherwise.
    """
    user_id = build_user_id(**fields)
    data = _load_auth_data()
    users = data.get("users", {})

    user = users.get(user_id)
    if not user:
        return None

    stored_hash = user.get("password_hash", "")
    stored_salt = user.get("password_salt", "")
    if stored_hash and stored_salt and verify_password(password, stored_hash, stored_salt):
        # Ensure user config exists (repair legacy registrations that
        # were created before config.json auto-generation was added).
        from .config_extension import ensure_user_config_exists
        ensure_user_config_exists(user_id)
        return create_token(user_id)
    return None


def get_user_info(user_id: str) -> Optional[Dict[str, str]]:
    """Get user info by user_id.

    Returns a dict with ``user_id`` plus all configured user fields,
    or ``{"user_id": ..., "username": ...}`` for legacy entries.
    """
    data = _load_auth_data()
    users = data.get("users", {})
    user = users.get(user_id)
    if not user:
        return None

    # Check if this is a multi-field user (has at least one USER_FIELDS key)
    has_user_fields = any(field in user for field in USER_FIELDS)
    if has_user_fields:
        result = {"user_id": user_id}
        for field in USER_FIELDS:
            result[field] = user.get(field, "")
        return result
    else:
        return {"user_id": user_id, "username": user.get("username", "")}


def update_credentials(user_id, current_password, new_password=None) -> Optional[str]:
    """Update a user's password."""
    data = _load_auth_data()
    users = data.get("users", {})
    user = users.get(user_id)
    if not user:
        return None

    stored_hash = user.get("password_hash", "")
    stored_salt = user.get("password_salt", "")
    if not verify_password(current_password, stored_hash, stored_salt):
        return None

    if new_password:
        pw_hash, salt = _hash_password(new_password)
        users[user_id]["password_hash"] = pw_hash
        users[user_id]["password_salt"] = salt
        data["jwt_secret"] = secrets.token_hex(32)

    data["users"] = users
    _save_auth_data(data)
    return create_token(user_id)


def delete_user(user_id: str, admin_user_id: str) -> bool:
    """Delete a user account (admin only)."""
    data = _load_auth_data()
    users = data.get("users", {})

    if user_id not in users:
        return False

    if len(users) <= 1:
        logger.warning("Cannot delete last user '%s'", user_id)
        return False

    del users[user_id]
    data["users"] = users
    _save_auth_data(data)
    return True


# ===================================================================
# Auto-register from environment variables
# ===================================================================


def auto_register_from_env() -> None:
    """Auto-register admin user from environment variables.

    For each field in ``USER_FIELDS``, the corresponding env var name
    is ``QWENPAW_AUTH_{FIELD_NAME_UPPER}`` (e.g. ``QWENPAW_AUTH_USERNAME``).
    """
    if not is_auth_enabled():
        return

    from .constants import ENV_AUTH_PASSWORD, ENV_AUTH_USERNAME

    env_fields = {}
    for field in USER_FIELDS:
        env_var = get_env_var_for_field(field)
        env_fields[field] = os.environ.get(env_var, "").strip()

    env_password = os.environ.get(ENV_AUTH_PASSWORD, "").strip()

    if all(env_fields.values()) and env_password:
        user_id = build_user_id(**env_fields)
        if user_id not in list_users():
            token = register_user(password=env_password, **env_fields)
            if token:
                logger.info("Auto-registered user from env vars: '%s'", user_id)
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
    """Multi-user auth middleware.

    In standalone mode (QWENPAW_AUTH_ENABLED=true):
      Validates HMAC tokens and routes requests to user workspaces.

    In integration mode (QWENPAW_AUTH_ENABLED=false):
      Skips token validation; parses user identity from upstream token
      via the pluggable TokenParser and routes accordingly.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request with multi-user routing."""
        if self._should_skip_auth(request):
            # Still resolve user when multi-user is on
            if is_multi_user_enabled():
                token = self._extract_token(request)
                if token:
                    from .token_parser import parse_token_to_user_id

                    user_id = parse_token_to_user_id(token) or "default"
                    request.state.user = user_id
                    request.state.user_id = user_id

                    from .user_context import set_current_user_id

                    set_current_user_id(user_id)

                    from qwenpaw.config.context import set_current_workspace_dir

                    user_dir = get_user_working_dir(user_id)
                    set_current_workspace_dir(user_dir)

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

        user_id = verify_token(token)
        if user_id is None:
            return Response(
                content=json.dumps({"detail": "Invalid or expired token"}),
                status_code=401,
                media_type="application/json",
            )

        request.state.user = user_id
        request.state.user_id = user_id

        from .user_context import set_current_user_id

        set_current_user_id(user_id)

        from qwenpaw.config.context import set_current_workspace_dir

        user_dir = get_user_working_dir(user_id)
        set_current_workspace_dir(user_dir)

        # Propagate X-Agent-Id to ContextVar so that
        # token_usage and agent_stats can resolve per-agent paths.
        agent_id = request.headers.get("X-Agent-Id")
        if agent_id:
            from qwenpaw.app.agent_context import set_current_agent_id
            set_current_agent_id(agent_id)

        logger.debug("Switched to user '%s' dir: %s", user_id, user_dir)
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
    """Replace ``qwenpaw.app.auth`` with our multi-user version.

    This makes ``from qwenpaw.app.auth import ...`` everywhere resolve to
    the multi-user implementations transparently.
    """
    import qwenpaw.app.auth as upstream_auth

    # Export our key symbols into the upstream namespace
    # so existing imports like `from ..app.auth import AuthMiddleware` still work
    upstream_auth.AuthMiddleware = AuthMiddleware
    upstream_auth.verify_token = verify_token
    upstream_auth.create_token = create_token
    upstream_auth.is_auth_enabled = is_auth_enabled
    upstream_auth.is_multi_user_enabled = is_multi_user_enabled
    upstream_auth.has_registered_users = has_registered_users
    upstream_auth.list_users = list_users
    upstream_auth.register_user = register_user
    upstream_auth.authenticate = authenticate
    upstream_auth.get_user_info = get_user_info
    upstream_auth.update_credentials = update_credentials
    upstream_auth.delete_user = delete_user
    upstream_auth.ensure_user_workspace = ensure_user_workspace
    upstream_auth.get_user_working_dir = get_user_working_dir
    upstream_auth.get_user_secret_dir = get_user_secret_dir
    upstream_auth.build_user_id = build_user_id
    upstream_auth.parse_user_id = parse_user_id
    upstream_auth.auto_register_from_env = auto_register_from_env
    upstream_auth.USER_FIELDS = USER_FIELDS
    upstream_auth.PUBLIC_PATHS = PUBLIC_PATHS
    upstream_auth.PUBLIC_PREFIXES = PUBLIC_PREFIXES

    # Run auto-registration if configured
    auto_register_from_env()

    logger.debug("[multi-user/auth] Patched qwenpaw.app.auth module")
