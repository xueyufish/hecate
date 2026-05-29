"""FastAPI dependency injection utilities.

Provides common dependencies used across API endpoints:
- Database session management (re-exports ``get_db``)
- Dual authentication: JWT Bearer Token (user) + API Key (service)
- Current Agent retrieval by path parameter
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.config import settings
from hecate.core.database import get_db
from hecate.models.agent import AgentModel
from hecate.services.auth.token import decode_access_token

security_scheme = HTTPBearer()


async def verify_api_key(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security_scheme)],
) -> str:
    """Verify the request via API Key or JWT Bearer token.

    Accepts both authentication methods for backward compatibility:
    - API Key: ``Authorization: Bearer <hecate-api-key>``
    - JWT: ``Authorization: Bearer <jwt-access-token>``

    Args:
        credentials: The HTTP Bearer credentials from the request header.

    Returns:
        str: The validated API key or JWT token string.

    Raises:
        HTTPException: 401 if neither API key nor JWT is valid.
    """
    token = credentials.credentials

    if token in settings.api_keys_list:
        return token

    try:
        decode_access_token(token)
        return token
    except (JWTError, ValueError, KeyError):
        pass

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


async def get_current_user_id(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security_scheme)],
) -> uuid.UUID:
    """Extract the user ID from a JWT Bearer access token.

    Tries JWT first; falls back to returning a placeholder if the token
    is an API key (for backward compatibility with API Key auth).

    Returns:
        UUID: The authenticated user's ID.

    Raises:
        HTTPException: 401 if the token is neither a valid JWT nor a valid API key.
    """
    token = credentials.credentials

    # Try JWT first
    try:
        payload = decode_access_token(token)
        return uuid.UUID(payload["sub"])
    except (JWTError, ValueError, KeyError):
        pass

    # Fallback to API Key
    if token in settings.api_keys_list:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "API Key auth not supported for user endpoints — use JWT",
                    "details": None,
                }
            },
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "error": {
                "code": "UNAUTHORIZED",
                "message": "Invalid or expired token",
                "details": None,
            }
        },
    )


async def get_current_agent(
    agent_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AgentModel:
    """Retrieve an agent by ID, raising 404 if not found or deleted.

    Args:
        agent_id: The UUID of the agent to retrieve.
        db: The async database session.

    Returns:
        AgentModel: The requested agent.

    Raises:
        HTTPException: 404 if the agent is not found or has been soft-deleted.
    """
    result = await db.execute(
        select(AgentModel).where(
            AgentModel.id == agent_id,
            AgentModel.deleted_at.is_(None),
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "NOT_FOUND",
                    "message": "Agent not found",
                    "details": None,
                }
            },
        )
    return agent
