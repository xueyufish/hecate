"""SCIM authentication — bearer token verification for SCIM endpoints."""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request, status

from hecate.core.config import settings

logger = logging.getLogger(__name__)


async def verify_scim_token(request: Request) -> str:
    """Verify SCIM bearer token from Authorization header.

    Args:
        request: FastAPI request object.

    Returns:
        The validated token string.

    Raises:
        HTTPException: 401 if token is missing or invalid.
    """
    if not settings.SCIM_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SCIM is not enabled",
        )

    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    parts = auth_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization format. Expected: Bearer <token>",
        )

    token = parts[1]
    if token != settings.SCIM_BEARER_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid SCIM token",
        )

    return token
