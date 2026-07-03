"""Auth resolver — iterates registered auth providers to authenticate requests.

The ``resolve_auth_context()`` function tries each registered provider
in order. The first to return a non-None AuthContext wins.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.auth.provider import AuthProviderABC
from hecate.core.auth_context import AuthContext

logger = logging.getLogger(__name__)

# Module-level provider list, populated by register_auth_providers()
_providers: list[AuthProviderABC] = []


def register_auth_providers(*providers: AuthProviderABC) -> None:
    """Register auth providers for use by resolve_auth_context().

    Args:
        *providers: AuthProviderABC instances in priority order.
    """
    _providers.clear()
    _providers.extend(providers)
    logger.info("Registered %d auth providers: %s", len(providers), [p.name for p in providers])


def get_registered_providers() -> list[AuthProviderABC]:
    """Return the currently registered auth providers."""
    return list(_providers)


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
