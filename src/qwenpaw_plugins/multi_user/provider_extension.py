# -*- coding: utf-8 -*-
"""Provider extension: makes ProviderManager user-aware for credentials.

Strategy
--------
Instead of replacing ProviderManager, we wrap its key methods so that:

- ``get_provider()`` returns a *copy* of the provider with user-specific
  API keys overlaid.
- ``update_provider()`` for builtin providers writes to the user's secret
  directory instead of mutating the shared registry.
- ``save_active_model()`` / ``load_active_model()`` use user paths.
- All other disk-writing methods (``add_custom_provider``, ``fetch_provider_models``,
  ``add_model_to_provider``, etc.) are overridden to persist to the user's
  own ``providers/`` directory under ``{SECRET_DIR}/users/{user_id}/``.

The shared builtin registry (model lists, base URLs, etc.) remains untouched.
Only *credentials* and *custom providers* are per-user.
"""
from __future__ import annotations

import json
import logging
import os
from copy import copy, deepcopy
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_real_provider_manager = None


class UserAwareProviderManager:
    """Wraps the real ProviderManager with user credential overlay."""

    def __init__(self, real_manager):
        self._real = real_manager

    def __getattr__(self, name: str):
        """Delegate any method not explicitly overridden to the real manager.

        This catches methods like ``start_local_model_resume`` and
        ``register_plugin_provider`` that the upstream _app.py calls during
        lifespan init — they are system-level and do not need per-user
        behaviour.
        """
        return getattr(self._real, name)

    # ------------------------------------------------------------------
    # User path helpers
    # ------------------------------------------------------------------

    def _get_user_paths(self) -> tuple:
        """Return (root, builtin, custom, active_model) paths for current user."""
        try:
            from .user_context import get_current_user_id
            from .auth_extension import get_user_secret_dir

            ctx = get_current_user_id()
            if ctx and ctx != "default":
                user_secret = get_user_secret_dir(ctx)
                root = user_secret / "providers"
                return root, root / "builtin", root / "custom", root / "active_model.json"
        except Exception:
            pass
        # Fallback to global paths
        from qwenpaw.constant import SECRET_DIR

        root = SECRET_DIR / "providers"
        return root, root / "builtin", root / "custom", root / "active_model.json"

    def _get_user_overrides(self) -> Dict:
        """Load per-user provider credential overrides from disk."""
        try:
            from .user_context import get_current_user_id
            from .auth_extension import get_user_secret_dir

            ctx = get_current_user_id()
            if not ctx or ctx == "default":
                return {}

            user_secret = get_user_secret_dir(ctx)
            overrides = {}

            for subdir in ("builtin", "custom"):
                user_subdir = user_secret / "providers" / subdir
                if user_subdir.is_dir():
                    for p_file in user_subdir.glob("*.json"):
                        try:
                            with open(p_file, "r", encoding="utf-8") as f:
                                data = json.load(f)
                            overrides[data.get("id", p_file.stem)] = data
                        except Exception as e:
                            logger.warning("Failed to load user override %s: %s", p_file, e)
            return overrides
        except Exception:
            return {}

    def _apply_overlay(self, provider):
        """Return a deep-copied provider with user credentials applied.

        The user override JSON (written by ``update_provider``) contains
        a full ``model_dump()`` of the provider.  We apply every field
        that the provider model actually declares — this ensures that
        ``base_url``, ``generate_kwargs``, etc. are also per-user, not
        just ``api_key``.
        """
        merged = copy(provider)
        if hasattr(provider, 'models'):
            merged.models = deepcopy(provider.models)
        if hasattr(provider, 'extra_models'):
            merged.extra_models = deepcopy(getattr(provider, 'extra_models', []))
        if hasattr(provider, 'generate_kwargs'):
            merged.generate_kwargs = dict(getattr(provider, 'generate_kwargs', {}))

        overrides = self._get_user_overrides()
        override = overrides.get(getattr(provider, 'id', ''))
        if override:
            # Apply scalar fields from the user override that are valid
            # on the provider model.  We skip complex nested fields like
            # ``models`` and ``extra_models`` because the override JSON
            # contains plain dicts (not ModelInfo objects) and Pydantic
            # may accept them without validation, causing AttributeError
            # later when code calls ``m.model_dump()`` on a plain dict.
            from qwenpaw.providers.provider import Provider
            valid_fields = set(Provider.model_fields.keys())
            # Fields that contain nested model objects — must not overlay
            # from raw JSON dicts.
            _SKIP_FIELDS = {"models", "extra_models"}
            for key, value in override.items():
                if key in valid_fields and key not in _SKIP_FIELDS and value is not None:
                    try:
                        setattr(merged, key, value)
                    except (ValueError, AttributeError):
                        # Skip fields that Pydantic rejects (e.g. frozen)
                        pass
        return merged

    # ------------------------------------------------------------------
    # Core: user-aware _save_provider / load_provider
    # ------------------------------------------------------------------

    def _user_save_provider(
        self,
        provider,
        is_builtin: bool = False,
        skip_if_exists: bool = False,
    ):
        """Save a provider config to the user's providers directory."""
        root, builtin_path, custom_path, _ = self._get_user_paths()
        provider_dir = builtin_path if is_builtin else custom_path
        provider_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(provider_dir, 0o700)
        except OSError:
            pass

        provider_path = provider_dir / f"{provider.id}.json"
        if skip_if_exists and provider_path.exists():
            return

        from qwenpaw.security.secret_store import (
            PROVIDER_SECRET_FIELDS,
            encrypt_dict_fields,
        )
        data = encrypt_dict_fields(
            provider.model_dump(),
            PROVIDER_SECRET_FIELDS,
        )
        with open(provider_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        try:
            os.chmod(provider_path, 0o600)
        except OSError:
            pass

    def _user_load_provider(
        self,
        provider_id: str,
        is_builtin: bool = False,
    ):
        """Load a provider config from the user's providers directory."""
        root, builtin_path, custom_path, _ = self._get_user_paths()
        provider_dir = builtin_path if is_builtin else custom_path
        provider_path = provider_dir / f"{provider_id}.json"
        if not provider_path.exists():
            return None
        try:
            with open(provider_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            from qwenpaw.security.secret_store import (
                PROVIDER_SECRET_FIELDS,
                decrypt_dict_fields,
            )
            data = decrypt_dict_fields(data, PROVIDER_SECRET_FIELDS)
            return self._real._provider_from_data(data)
        except Exception as e:
            logger.warning(
                "Failed to load user provider '%s' from %s: %s",
                provider_id,
                provider_path,
                e,
            )
            return None

    # ------------------------------------------------------------------
    # Delegated methods (with overlay or user routing)
    # ------------------------------------------------------------------

    def get_provider(self, provider_id: str):
        base = self._real.get_provider(provider_id)
        if base is None:
            return None
        return self._apply_overlay(base)

    async def get_provider_info(self, provider_id: str):
        provider = self.get_provider(provider_id)
        return await provider.get_info() if provider else None

    async def list_provider_info(self) -> List:
        tasks = [self._apply_overlay(p).get_info() for p in self._real.builtin_providers.values()]
        tasks += [self._apply_overlay(p).get_info() for p in self._real.custom_providers.values()]
        import asyncio
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if not isinstance(r, Exception)]

    # --- Active model (user-aware) ---

    def get_active_model(self):
        # Per-user: check user-specific active_model.json first
        _, _, _, active_path = self._get_user_paths()
        try:
            from .user_context import get_current_user_id
            ctx = get_current_user_id()
            if ctx and ctx != "default" and active_path.exists():
                with open(active_path, "r") as f:
                    data = json.load(f)
                return self._real.active_model.__class__.model_validate(data)
        except Exception as exc:
            logger.debug("Failed to load per-user active model: %s", exc)
        return self._real.get_active_model()

    async def activate_model(self, provider_id, model_id):
        provider = self.get_provider(provider_id)
        if not provider:
            raise ValueError(f"Provider '{provider_id}' not found.")
        active_model = self._real.active_model.__class__(provider_id=provider_id, model=model_id)
        # Save to user-specific path only — do NOT mutate the global singleton
        self.save_active_model(active_model)
        # Trigger multimodal probe (same as upstream behavior)
        self._real.maybe_probe_multimodal(provider_id, model_id)

    def save_active_model(self, active_model):
        _, _, _, active_path = self._get_user_paths()
        active_path.parent.mkdir(parents=True, exist_ok=True)
        with open(active_path, "w", encoding="utf-8") as f:
            json.dump(active_model.model_dump(), f, indent=2, ensure_ascii=False)

    def load_active_model(self):
        _, _, _, active_path = self._get_user_paths()
        if not active_path.exists():
            return None
        import json as _j
        with open(active_path, "r") as f:
            data = _j.load(f)
        return self._real.active_model.__class__.model_validate(data)

    def clear_active_model(self, provider_id: str | None = None) -> bool:
        """Clear the active model, removing from user directory."""
        current = self.get_active_model()
        if current is None:
            return False
        if provider_id is not None:
            provider_id = self._real._normalize_provider_id(provider_id)
        if provider_id is not None and current.provider_id != provider_id:
            return False
        _, _, _, active_path = self._get_user_paths()
        try:
            active_path.unlink()
        except (FileNotFoundError, OSError):
            pass
        return True

    # --- Provider config update (user-aware) ---

    def update_provider(self, provider_id: str, config: Dict) -> bool:
        """Update provider config, persisted to user directory."""
        provider_id = self._real._normalize_provider_id(provider_id)

        # For default user: delegate to the real manager so that the
        # in-memory global registry is also updated (same as upstream).
        # This ensures that get_provider() returns the new values without
        # relying on the overlay path (which is skipped for "default").
        try:
            from .user_context import get_current_user_id
            ctx = get_current_user_id()
        except Exception:
            ctx = None
        if not ctx or ctx == "default":
            return self._real.update_provider(provider_id, config)

        # For non-default users: save to user path without touching
        # the shared registry — the overlay in _apply_overlay will pick
        # up the new values from the user directory on next read.
        if provider_id in getattr(self._real, 'builtin_providers', {}):
            provider_copy = copy(self._real.builtin_providers[provider_id])
            provider_copy.update_config(config)
            self._save_provider_to_user(provider_copy, is_builtin=True)
            return True

        # For plugin providers: delegate to real manager (system-level)
        if provider_id in getattr(self._real, 'plugin_providers', {}):
            return self._real.update_provider(provider_id, config)

        provider = self._real.custom_providers.get(provider_id)
        if not provider:
            return False
        # Copy before update_config to avoid mutating the shared registry
        provider_copy = copy(provider)
        provider_copy.update_config(config)
        self._save_provider_to_user(provider_copy, is_builtin=False)
        return True

    def _save_provider_to_user(self, provider, is_builtin=False):
        """Save provider to user directory (used by update_provider)."""
        _, builtin_path, custom_path, _ = self._get_user_paths()
        provider_dir = builtin_path if is_builtin else custom_path
        provider_dir.mkdir(parents=True, exist_ok=True)
        path = provider_dir / f"{provider.id}.json"
        data = provider.model_dump()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # --- Custom providers (user-aware) ---

    async def add_custom_provider(self, provider_data):
        """Add a custom provider, persisted to the user directory."""
        provider_payload = provider_data.model_dump()
        provider_payload["id"] = self._real._resolve_custom_provider_id(
            provider_data.id,
        )
        provider_payload["is_custom"] = True
        provider = self._real._provider_from_data(provider_payload)
        provider.support_connection_check = False
        self._real.custom_providers[provider.id] = provider
        # Save to user directory (not global)
        self._user_save_provider(provider, is_builtin=False)
        return await provider.get_info()

    def remove_custom_provider(self, provider_id: str) -> bool:
        """Remove a custom provider from both memory and user directory."""
        if provider_id in self._real.custom_providers:
            del self._real.custom_providers[provider_id]
            # Also remove from user directory
            _, _, custom_path, _ = self._get_user_paths()
            provider_file = custom_path / f"{provider_id}.json"
            if provider_file.exists():
                try:
                    os.remove(provider_file)
                except OSError:
                    pass
            return True
        return False

    # --- Model management (user-aware) ---

    async def add_model_to_provider(self, provider_id: str, model_info) -> object:
        """Add a model to a provider, persisted to user directory."""
        provider_id = self._real._normalize_provider_id(provider_id)
        provider = self.get_provider(provider_id)
        if not provider:
            from qwenpaw.providers.errors import ProviderError
            raise ProviderError(message=f"Provider '{provider_id}' not found.")
        await provider.add_model(model_info)
        is_builtin = provider_id in self._real.builtin_providers
        # Update in-memory registry
        if is_builtin and provider_id in self._real.builtin_providers:
            self._real.builtin_providers[provider_id] = provider
        elif provider_id in self._real.custom_providers:
            self._real.custom_providers[provider_id] = provider
        # Save to user directory
        self._user_save_provider(provider, is_builtin=is_builtin)
        return await provider.get_info()

    async def delete_model_from_provider(self, provider_id: str, model_id: str) -> object:
        """Delete a model from a provider, persisted to user directory."""
        provider_id = self._real._normalize_provider_id(provider_id)
        provider = self.get_provider(provider_id)
        if not provider:
            from qwenpaw.providers.errors import ProviderError
            raise ProviderError(message=f"Provider '{provider_id}' not found.")
        await provider.delete_model(model_id=model_id)
        is_builtin = provider_id in self._real.builtin_providers
        if is_builtin and provider_id in self._real.builtin_providers:
            self._real.builtin_providers[provider_id] = provider
        elif provider_id in self._real.custom_providers:
            self._real.custom_providers[provider_id] = provider
        self._user_save_provider(provider, is_builtin=is_builtin)
        return await provider.get_info()

    async def update_model_config(self, provider_id: str, model_id: str, config: Dict) -> object:
        """Update per-model config, persisted to user directory."""
        provider_id = self._real._normalize_provider_id(provider_id)
        provider = self.get_provider(provider_id)
        if not provider:
            from qwenpaw.providers.errors import ProviderError
            raise ProviderError(message=f"Provider '{provider_id}' not found.")
        if not provider.update_model_config(model_id, config):
            from qwenpaw.providers.errors import ModelNotFoundException
            raise ModelNotFoundException(
                model_name=f"{provider_id}/{model_id}",
                details={"provider_id": provider_id, "model_id": model_id},
            )
        is_builtin = provider_id in self._real.builtin_providers
        if is_builtin and provider_id in self._real.builtin_providers:
            self._real.builtin_providers[provider_id] = provider
        elif provider_id in self._real.custom_providers:
            self._real.custom_providers[provider_id] = provider
        self._user_save_provider(provider, is_builtin=is_builtin)
        return await provider.get_info()

    async def fetch_provider_models(
        self,
        provider_id: str,
        save: bool = True,
    ) -> List:
        """Fetch models from a provider, optionally persisted to user directory."""
        provider_id = self._real._normalize_provider_id(provider_id)
        provider = self.get_provider(provider_id)
        if not provider:
            return []
        try:
            models = await provider.fetch_models()
            if save:
                provider.extra_models = models
                is_builtin = provider_id in self._real.builtin_providers
                if is_builtin and provider_id in self._real.builtin_providers:
                    self._real.builtin_providers[provider_id] = provider
                elif provider_id in self._real.custom_providers:
                    self._real.custom_providers[provider_id] = provider
                self._user_save_provider(provider, is_builtin=is_builtin)
            return models
        except Exception as e:
            logger.warning(
                "Failed to fetch models for provider '%s': %s",
                provider_id,
                e,
            )
            return []

    async def probe_model_multimodal(
        self,
        provider_id: str,
        model_id: str,
    ) -> dict:
        """Probe a model's multimodal capabilities, persisted to user directory."""
        provider_id = self._real._normalize_provider_id(provider_id)
        provider = self.get_provider(provider_id)
        if not provider:
            return {"error": f"Provider '{provider_id}' not found"}

        result = await provider.probe_model_multimodal(model_id)

        # Update the model's capability flags
        for model in provider.models + provider.extra_models:
            if model.id == model_id:
                model.supports_image = result.supports_image
                model.supports_video = result.supports_video
                model.supports_multimodal = result.supports_multimodal
                model.probe_source = "probed"
                break

        # Persist to user directory
        is_builtin = provider_id in self._real.builtin_providers
        if is_builtin and provider_id in self._real.builtin_providers:
            self._real.builtin_providers[provider_id] = provider
        elif provider_id in self._real.custom_providers:
            self._real.custom_providers[provider_id] = provider
        self._user_save_provider(provider, is_builtin=is_builtin)

        return {
            "supports_image": result.supports_image,
            "supports_video": result.supports_video,
            "supports_multimodal": result.supports_multimodal,
            "image_message": result.image_message,
            "video_message": result.video_message,
        }

    def maybe_probe_multimodal(self, provider_id: str, model_id: str) -> None:
        """Schedule multimodal probing for a Model if capability is unknown."""
        import asyncio
        provider = self.get_provider(provider_id)
        if not provider:
            return
        for model in provider.models + provider.extra_models:
            if model.id == model_id and model.supports_multimodal is None:
                asyncio.create_task(
                    self.probe_model_multimodal(provider_id, model_id),
                )
                break


def patch_provider_manager(real_manager) -> None:
    """Wrap the existing ProviderManager singleton.

    This patches both ``app.state.provider_manager`` (used by FastAPI
    Depends injection) *and* ``ProviderManager._instance`` / ``get_instance()``
    (used by model_factory.py and other code that bypasses DI).

    Without patching ``_instance``, calls like
    ``ProviderManager.get_instance().get_provider(id)`` would return the
    original provider with **no** user credential overlay — a security
    leak that lets every user see the global API key.
    """
    global _real_provider_manager
    _real_provider_manager = UserAwareProviderManager(real_manager)

    # Patch the singleton so ProviderManager.get_instance() returns the
    # wrapped version — this is critical because model_factory.py and
    # model_handler.py call get_instance() directly.
    from qwenpaw.providers.provider_manager import ProviderManager
    ProviderManager._instance = _real_provider_manager

    # Patch get_active_chat_model() — it's a @staticmethod that calls
    # get_instance() internally, but some call-sites may have already
    # imported the old bound method.  Replacing it ensures every call
    # goes through the user-aware manager.
    _original_get_active_chat_model = ProviderManager.get_active_chat_model

    @staticmethod
    def _user_aware_get_active_chat_model():
        """User-aware replacement for ProviderManager.get_active_chat_model."""
        manager = ProviderManager.get_instance()
        model = manager.get_active_model()
        if model is None or model.provider_id == "" or model.model == "":
            from qwenpaw.providers.errors import ProviderError
            raise ProviderError(message="No active model configured.")
        provider = manager.get_provider(model.provider_id)
        if provider is None:
            from qwenpaw.providers.errors import ProviderError
            raise ProviderError(
                message=f"Active provider '{model.provider_id}' not found."
            )
        return provider.get_chat_model_instance(model.model)

    ProviderManager.get_active_chat_model = _user_aware_get_active_chat_model

    logger.info("[multi-user/provider] Wrapped ProviderManager with user overlays")


async def wrap_provider_for_user(app, provider_manager_instance):
    """Lifespan hook callback for ``post_provider_init``.

    This is registered via :func:`qwenpaw_plugins.register_lifespan_hook`
    and called by ``_app.py``'s lifespan right after the ProviderManager
    is obtained, before it is stored in ``app.state``.

    Parameters
    ----------
    app:
        The FastAPI application instance (unused but required by hook signature).
    provider_manager_instance:
        The ProviderManager instance (from ``ProviderManager.get_instance()``).

    Returns
    -------
    The user-aware wrapper around the provider manager.
    """
    patch_provider_manager(provider_manager_instance)
    return get_wrapped_provider_manager()


def get_wrapped_provider_manager():
    """Return the wrapped provider manager (or None)."""
    return _real_provider_manager
