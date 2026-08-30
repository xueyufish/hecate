"""Auth resolver — iterates registered auth providers to authenticate requests.

The ``resolve_auth_context()`` function tries each registered provider
in order. The first to return a non-None AuthContext wins.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from importlib.metadata import entry_points

from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.auth.provider import AuthProvider
from hecate.core.auth_context import AuthContext

logger = logging.getLogger(__name__)

# Module-level provider list, populated by register_auth_providers()
_providers: list[AuthProvider] = []


def register_auth_providers(*providers: AuthProvider) -> None:
    """Register auth providers for use by resolve_auth_context().

    Args:
        *providers: AuthProvider instances in priority order.
    """
    _providers.clear()
    _providers.extend(providers)
    logger.info("Registered %d auth providers: %s", len(providers), [p.name for p in providers])


def get_registered_providers() -> list[AuthProvider]:
    """Return the currently registered auth providers."""
    return list(_providers)


def load_entry_point_providers(gate: Callable[[], bool] | None = None) -> list[AuthProvider]:
    """Discover auth providers via the ``hecate.auth_providers`` entry-point group.

    Each entry point loads to a zero-arg factory that reads its own settings
    and returns a provider instance, or ``None`` when unconfigured (the
    factory contract is the Airflow get_provider_info / Kedro hooks
    precedent). The host skips ``None``. ``gate`` is the future license-
    check hook; this PR passes ``None`` — pure discovery.

    Args:
        gate: Optional predicate consulted before scanning; if it returns
            ``False`` the scan is skipped (used by future license gating).
    """
    if gate is not None and not gate():
        return []
    discovered: list[AuthProvider] = []
    for ep in entry_points(group="hecate.auth_providers"):
        try:
            instance = ep.load()()
        except Exception:
            logger.exception("Failed to load auth provider entry point %s", ep.name)
            continue
        if instance is not None:
            discovered.append(instance)
    return discovered


async def resolve_auth_context(
    credentials: HTTPAuthorizationCredentials,
    db: AsyncSession,
) -> AuthContext:
    """Resolve authentication context by iterating registered providers.

    Tries each provider in registration order. The first to return a
    non-None AuthContext wins. If no provider succeeds, raises HTTP 401.

    Args:
        credentials: The HTTP Bearer credentials.
        db: Async database session.

    Returns:
        AuthContext from the first successful provider.

    Raises:
        HTTPException: 401 if no provider authenticates the token.
    """
    token = credentials.credentials

    for provider in _providers:
        try:
            ctx = await provider.authenticate(token, db)
            if ctx is not None:
                logger.debug("Authenticated via %s provider", provider.name)
                return ctx
        except Exception:
            logger.debug("Provider %s failed, trying next", provider.name, exc_info=True)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "error": {
                "code": "UNAUTHORIZED",
                "message": "Invalid API key or token",
                "details": None,
            }
        },
    )
