"""A2A authentication middleware for APIKey and HTTP Bearer."""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from hecate.core.config import settings

logger = logging.getLogger(__name__)

api_key_header = HTTPBearer(auto_error=False)


async def verify_a2a_auth(request: Request) -> str | None:
    """Verify A2A request authentication.

    Supports two modes:
    - api_key: Checks X-API-Key header against HECATE_API_KEYS
    - bearer: Checks Authorization: Bearer <token> header

    Args:
        request: The incoming FastAPI request.

    Returns:
        The authenticated API key or token.

    Raises:
        HTTPException: If authentication fails.
    """
    if settings.A2A_AUTH_MODE == "none":
        return None

    # Try API key first
    api_key = request.headers.get("X-API-Key")
    if api_key:
        if api_key in settings.api_keys_list:
            return api_key
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Try Bearer token
    credentials: HTTPAuthorizationCredentials | None = await api_key_header(request)
    if credentials and credentials.credentials:
        token = credentials.credentials
        if token in settings.api_keys_list:
            return token
        raise HTTPException(status_code=401, detail="Invalid bearer token")

    # No credentials provided
    raise HTTPException(
        status_code=401,
        detail="Missing authentication credentials",
        headers={"WWW-Authenticate": 'Bearer realm="a2a"'},
    )
