# -*- coding: utf-8 -*-
"""Middleware factory: creates and returns the AuthMiddleware instance.

This module exists to avoid circular imports between __init__.py and
auth_extension.py.
"""
from __future__ import annotations

from fastapi import FastAPI
from .auth_extension import AuthMiddleware


def create_auth_middleware(app: FastAPI | None = None) -> AuthMiddleware:
    """Create and return an AuthMiddleware instance.

    Args:
        app: The FastAPI application instance (required for Starlette >= 0.33).

    Returns:
        AuthMiddleware: The configured middleware ready for app.add_middleware().
    """
    if app is not None:
        return AuthMiddleware(app)
    # Legacy fallback for older Starlette versions that don't require app.
    return AuthMiddleware()  # type: ignore[call-arg]
