# -*- coding: utf-8 -*-
"""Token parser for extracting user identity from business-system tokens.

Ported from CoPaw's ``copaw.app.token_parser`` with naming adapted to
qwenpaw_plugins.  This module is self-contained and has **no** dependency
on the upstream ``qwenpaw`` package — it only depends on
``auth_extension`` for the user ID helpers.

Architecture
------------

``TokenParser`` is an abstract base class with a single method
:meth:`parse`.  The default implementation,
:class:`DefaultTokenParser`, handles common JWT payload structures.

Business-system integration
--------------------------
To use your own token format:

1. Subclass :class:`TokenParser` and implement :meth:`parse`.
2. Register your implementation via one of these methods:

   - **Environment variable**: Set ``QWENPAW_TOKEN_PARSER_MODULE`` to the
     dotted path of your module.
   - **Runtime registration**: Call :func:`set_token_parser` with your
     instance before the first request arrives.

Default parser behaviour
------------------------
The :class:`DefaultTokenParser` tries:

1. **JWT payload** — decode the payload (no signature verification) and
   look for the configured user fields or a ``sub`` claim containing the
   composite ID.
2. **Direct composite** — treat the token as a
   slash-separated composite string matching the configured ``USER_FIELDS``.
3. **Fallback** — return ``None`` → middleware uses ``"default"``.
"""
from __future__ import annotations

import abc
import importlib
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Import user helpers lazily to avoid circular imports at module level.
# The actual imports happen inside the functions that need them.


def _get_user_fields():
    """Lazy-import USER_FIELDS from constants."""
    from .constants import USER_FIELDS  # noqa: F811
    return USER_FIELDS


def _get_build_user_id():
    """Lazy-import build_user_id from auth_extension."""
    from .auth_extension import build_user_id
    return build_user_id


def _get_parse_user_id():
    """Lazy-import parse_user_id from auth_extension."""
    from .auth_extension import parse_user_id
    return parse_user_id


class TokenParser(abc.ABC):
    """Abstract base class for token parsing.

    Subclass this to implement custom token-to-user resolution logic.
    """

    @abc.abstractmethod
    def parse(self, token: str) -> Optional[dict]:
        """Parse *token* and return user identity fields.

        Parameters
        ----------
        token:
            The raw token string (``Bearer`` prefix already stripped).

        Returns
        -------
        dict or None
            A dict whose keys match the configured ``USER_FIELDS``
            (default: ``username``); or ``None`` if unparseable.
        """


class DefaultTokenParser(TokenParser):
    """Default token parser handling JWT payloads and composite IDs."""

    def parse(self, token: str) -> Optional[dict]:
        """Try JWT decoding first, then composite string parsing."""
        jwt_result = self._try_parse_jwt(token)
        if jwt_result is not None:
            logger.debug("DefaultTokenParser: parsed user from JWT: %s", jwt_result)
            return jwt_result

        composite_result = self._try_parse_composite(token)
        if composite_result is not None:
            logger.debug(
                "DefaultTokenParser: parsed user from composite: %s",
                composite_result,
            )
            return composite_result

        logger.debug("DefaultTokenParser: could not parse token")
        return None

    @staticmethod
    def _try_parse_jwt(token: str) -> Optional[dict]:
        """Decode JWT payload (no sig verification) and extract fields."""
        try:
            import base64

            parts = token.split(".")
            if len(parts) != 3:
                return None

            payload_b64 = parts[1]
            padding = 4 - len(payload_b64) % 4
            if padding != 4:
                payload_b64 += "=" * padding

            payload_bytes = base64.urlsafe_b64decode(payload_b64)
            payload = json.loads(payload_bytes)
        except (ValueError, json.JSONDecodeError, Exception) as exc:
            logger.debug("Failed to decode JWT payload: %s", exc)
            return None

        if not isinstance(payload, dict):
            return None

        USER_FIELDS = _get_user_fields()
        fields = {}
        for field in USER_FIELDS:
            value = payload.get(field)
            if value and isinstance(value, str):
                fields[field] = value.strip()

        if len(fields) == len(USER_FIELDS) and all(fields.values()):
            return fields

        sub = payload.get("sub")
        if sub and isinstance(sub, str):
            try:
                parse_user_id = _get_parse_user_id()
                return parse_user_id(sub.strip())
            except ValueError:
                pass

        return None

    @staticmethod
    def _try_parse_composite(token: str) -> Optional[dict]:
        """Parse a slash-separated composite user ID string.

        The number of segments must match ``len(USER_FIELDS)``.
        """
        try:
            parse_user_id = _get_parse_user_id()
            return parse_user_id(token.strip())
        except (ValueError, AttributeError):
            return None


# ---------------------------------------------------------------------------
# Global singleton management
# ---------------------------------------------------------------------------

_token_parser_instance: Optional[TokenParser] = None


def get_token_parser() -> TokenParser:
    """Return the global :class:`TokenParser` singleton.

    Resolution order:
    1. Previously registered instance.
    2. Custom module from ``QWENPAW_TOKEN_PARSER_MODULE`` env var.
    3. :class:`DefaultTokenParser` fallback.
    """
    global _token_parser_instance
    if _token_parser_instance is not None:
        return _token_parser_instance

    from .constants import ENV_TOKEN_PARSER_MODULE

    module_path = os.environ.get(ENV_TOKEN_PARSER_MODULE, "").strip()
    if module_path:
        try:
            _token_parser_instance = _load_custom_parser(module_path)
            return _token_parser_instance
        except Exception as exc:
            logger.warning(
                "Failed to load custom token parser from '%s': %s",
                module_path,
                exc,
            )

    _token_parser_instance = DefaultTokenParser()
    return _token_parser_instance


def set_token_parser(parser: TokenParser) -> None:
    """Replace the global token parser."""
    global _token_parser_instance
    if not isinstance(parser, TokenParser):
        raise TypeError(
            f"set_token_parser() expects TokenParser, got {type(parser).__name__}"
        )
    _token_parser_instance = parser
    logger.info("Token parser replaced: %s", type(parser).__name__)


def reset_token_parser() -> None:
    """Reset the global token parser singleton (for testing)."""
    global _token_parser_instance
    _token_parser_instance = None


def _load_custom_parser(module_path: str) -> TokenParser:
    """Load custom token parser from a Python module path."""
    module = importlib.import_module(module_path)

    factory = getattr(module, "create_token_parser", None)
    if callable(factory):
        parser = factory()
        if isinstance(parser, TokenParser):
            return parser
        raise TypeError(
            f"create_token_parser() must return TokenParser, "
            f"got {type(parser).__name__}"
        )

    for attr_name in ("token_parser", "parser"):
        attr = getattr(module, attr_name, None)
        if isinstance(attr, TokenParser):
            return attr

    raise AttributeError(
        f"Module '{module_path}' must export 'create_token_parser()' or "
        f"a 'token_parser' attribute of type TokenParser"
    )


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


def parse_token_to_user_id(token: str) -> Optional[str]:
    """Parse token and return composite user ID string."""
    parser = get_token_parser()
    fields = parser.parse(token)
    if fields is None:
        return None
    build_user_id = _get_build_user_id()
    return build_user_id(**fields)


def parse_token_to_user_fields(token: str) -> Optional[dict]:
    """Parse token and return user fields dict."""
    return get_token_parser().parse(token)


def extract_bearer_token(authorization_header: str) -> str:
    """Extract raw token from ``Bearer <token>`` header value."""
    if authorization_header and authorization_header.startswith("Bearer "):
        return authorization_header[7:].strip()
    return ""


def parse_request_user_fields(authorization_header: str) -> Optional[dict]:
    """Canonical entry point: parse user identity from Authorization header."""
    token = extract_bearer_token(authorization_header)
    if not token:
        return None
    return get_token_parser().parse(token)


__all__ = [
    "TokenParser",
    "DefaultTokenParser",
    "get_token_parser",
    "set_token_parser",
    "reset_token_parser",
    "parse_token_to_user_id",
    "parse_token_to_user_fields",
    "extract_bearer_token",
    "parse_request_user_fields",
]
