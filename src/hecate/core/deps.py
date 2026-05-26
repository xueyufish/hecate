"""FastAPI dependency injection utilities.

Provides common dependencies used across API endpoints:
- Database session management (re-exports ``get_db``)
- API Key authentication via ``Authorization: Bearer <key>`` header
- Current Agent retrieval by path parameter
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.config import settings
from hecate.core.database import get_db
from hecate.models.agent import AgentModel

security_scheme = HTTPBearer()


async def verify_api_key(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security_scheme)],
) -> str:
    """Verify the API key from the Authorization header.

    Args:
        credentials: The HTTP Bearer credentials from the request header.

    Returns:
        str: The validated API key.

    Raises:
        HTTPException: 401 if the API key is missing or invalid.
    """
    api_key = credentials.credentials
    if api_key not in settings.api_keys_list:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "Invalid API key",
                    "details": None,
                }
            },
        )
    return api_key


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
