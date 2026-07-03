"""Auth provider registration — register built-in auth providers with PluginRegistry."""

from __future__ import annotations

import logging

from hecate.auth.api_key_provider import APIKeyAuthProvider
from hecate.auth.jwt_provider import JWTAuthProvider
from hecate.auth.provider import AuthProviderABC
from hecate.plugin.manifest import PluginManifest
from hecate.plugin.registry import PluginRegistry

logger = logging.getLogger(__name__)


def register_auth_providers(registry: PluginRegistry) -> int:
    """Register built-in auth providers with the plugin registry.

    Args:
        registry: The PluginRegistry to register providers with.

    Returns:
        Number of providers registered.
    """
    provider_instances: list[AuthProviderABC] = [
        JWTAuthProvider(),
        APIKeyAuthProvider(),
    ]

    count = 0
    for provider in provider_instances:
        try:
            manifest = PluginManifest(
                type="auth_provider",
                name=provider.name,
                version="1.0.0",
                api_version="1.0",
                min_platform_version="0.6.0",
                description=provider.description,
            )
            registry.register(manifest, provider)
            count += 1
        except Exception:
            logger.exception("Failed to register auth provider %s", provider.name)

    logger.info("Registered %d built-in auth providers", count)
    return count
