# -*- coding: utf-8 -*-
"""QwenPaw Plugins package.

This package contains optional plugin modules that extend QwenPaw's
functionality without modifying any upstream source files.

Available plugins:
- ``multi_tenant``: Multi-user / multi-tenant support with isolated
  workspaces per tenant.

Lifespan Hooks
--------------
Plugins that need to intercept the application lifespan (e.g. to wrap
managers created during startup) should register callbacks via
:func:`register_lifespan_hook`.  The upstream ``_app.py`` lifespan
calls :func:`run_lifespan_hooks` at well-defined points so that
plugins can modify managers **before** they are used.

Currently supported hook points:

- ``"post_manager_init"`` — called right after ``MultiAgentManager()``
  is created, before ``start_all_configured_agents()``.
  Receives ``(app, multi_agent_manager)`` and must return the
  (possibly wrapped) manager instance.

- ``"post_provider_init"`` — called right after
  ``ProviderManager.get_instance()`` is obtained, before it is stored
  in ``app.state``.  Receives ``(app, provider_manager)`` and must
  return the (possibly wrapped) manager instance.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Registered lifespan hooks, keyed by hook point name.
#: Each value is a list of callables in registration order.
_LIFESPAN_HOOKS: Dict[str, List[Callable]] = {}


def register_lifespan_hook(hook_point: str, callback: Callable) -> None:
    """Register a lifespan hook callback.

    Parameters
    ----------
    hook_point:
        One of the supported hook point names (e.g. ``"post_manager_init"``).
    callback:
        An async callable.  The signature depends on the hook point:
        - ``"post_manager_init"``: ``async (app, manager) -> manager``
        - ``"post_provider_init"``: ``async (app, provider_manager) -> provider_manager``
    """
    _LIFESPAN_HOOKS.setdefault(hook_point, []).append(callback)
    logger.debug("[plugins] Registered lifespan hook '%s': %s", hook_point, callback)


async def run_lifespan_hooks(hook_point: str, app: Any, obj: Any) -> Any:
    """Run all registered callbacks for *hook_point*.

    Each callback receives ``(app, obj)`` and must return the (possibly
    modified) object.  The return value of the last callback becomes the
    final result.

    Parameters
    ----------
    hook_point:
        The hook point name.
    app:
        The FastAPI application instance.
    obj:
        The object to pass through the hook chain (e.g. a manager).

    Returns
    -------
    The object after all hooks have been applied.
    """
    hooks = _LIFESPAN_HOOKS.get(hook_point, [])
    for hook in hooks:
        try:
            result = hook(app, obj)
            import inspect
            if inspect.iscoroutine(result) or inspect.isawaitable(result):
                result = await result
            obj = result
        except Exception as exc:
            logger.error(
                "[plugins] Lifespan hook '%s' (%s) failed: %s",
                hook_point,
                hook,
                exc,
                exc_info=True,
            )
    return obj
