"""Vault resolver — secret resolution with caching and Settings fallback.

Iterates registered SecretProviders in priority order, caches
static secrets with TTL, and falls back to Settings environment variables.
"""

from __future__ import annotations

import logging
import time

from hecate.core.config import settings

logger = logging.getLogger(__name__)

_providers: list = []
_cache: dict[str, tuple[float, str]] = {}


def register_providers(*providers: object) -> None:
    """Register secret providers for use by resolve_secret()."""
    _providers.clear()
    _providers.extend(providers)
    logger.info("Registered %d secret providers", len(providers))


def get_registered_providers() -> list:
    """Return the currently registered secret providers."""
    return list(_providers)


async def resolve_secret(path: str) -> str | None:
    """Resolve a secret by iterating providers with caching.

    Checks in-memory cache first (TTL from VAULT_CACHE_TTL).
    On cache miss, iterates providers in priority order.
    Falls back to Settings if no provider returns a value.

    Args:
        path: Secret path or key name.

    Returns:
        Secret value, or None if not found.
    """
    now = time.monotonic()
    cached = _cache.get(path)
    if cached and (now - cached[0]) < settings.VAULT_CACHE_TTL:
        return cached[1]

    for provider in _providers:
        try:
            value = await provider.get_secret(path)
            if value is not None:
                _cache[path] = (now, value)
                return value
        except Exception:
            logger.debug("Provider %s failed for %s", provider.name, path, exc_info=True)

    # Fall back to Settings
    fallback = _get_from_settings(path)
    if fallback is not None:
        _cache[path] = (now, fallback)
        return fallback

    return None


async def resolve_dynamic_credentials(role: str) -> dict[str, str] | None:
    """Resolve dynamic credentials without caching.

    Dynamic credentials have limited lease duration and must
    always be fetched fresh.

    Args:
        role: Role identifier for credential generation.

    Returns:
        Dict with credential fields, or None.
    """
    for provider in _providers:
        try:
            creds = await provider.get_dynamic_credentials(role)
            if creds is not None:
                return creds
        except Exception:
            logger.debug("Provider %s failed for dynamic creds %s", provider.name, role, exc_info=True)

    return None


def _get_from_settings(path: str) -> str | None:
    """Try to get a secret from Settings environment variables."""
    # Map common secret paths to Settings attributes
    settings_map = {
        "database/url": settings.DATABASE_URL,
        "jwt/secret": settings.JWT_SECRET,
        "qdrant/api-key": settings.QDRANT_API_KEY,
        "minio/access-key": settings.MINIO_ACCESS_KEY,
        "minio/secret-key": settings.MINIO_SECRET_KEY,
    }
    return settings_map.get(path)


def clear_cache() -> None:
    """Clear the secret cache."""
    _cache.clear()
