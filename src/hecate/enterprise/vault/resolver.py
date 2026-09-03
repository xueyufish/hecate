"""Vault resolver — secret resolution with caching and Settings fallback.

Iterates registered SecretProviders in priority order, caches
static secrets with TTL, and falls back to Settings environment variables.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from importlib.metadata import entry_points

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


def load_entry_point_providers(gate: Callable[[], bool] | None = None) -> list:
    """Discover secret providers via the ``hecate.secret_providers`` entry-point group.

    Same zero-arg-factory contract as the auth variant (Airflow
    get_provider_info / Kedro hooks precedent): each entry point loads to
    a callable that reads its own settings and returns a provider instance
    or ``None`` when unconfigured. The host skips ``None``. ``gate`` is
    the future license-check hook; this PR passes ``None``.

    Args:
        gate: Optional predicate consulted before scanning; if it returns
            ``False`` the scan is skipped (used by future license gating).
    """
    if gate is not None and not gate():
        return []
    discovered: list = []
    for ep in entry_points(group="hecate.secret_providers"):
        try:
            instance = ep.load()()
        except Exception:
            logger.exception("Failed to load secret provider entry point %s", ep.name)
            continue
        if instance is not None:
            discovered.append(instance)
    return discovered


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
