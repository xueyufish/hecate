"""Session management API endpoints.

Provides operations for sessions:
- ``POST /api/sessions`` — Create a new session
- ``GET /api/sessions`` — List sessions (paginated)
- ``GET /api/sessions/{id}`` — Get session by ID
- ``POST /api/sessions/{id}/resume`` — Resume an interrupted session
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.deps import get_db, verify_api_key
from hecate.models.session import SessionCreateSchema, SessionModel, SessionReadSchema

router = APIRouter()


class ResumeRequest(BaseModel):
    """Request body for resuming an interrupted session."""

    resume_value: str


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    data: SessionCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """Create a new session.

    Args:
        data: The session creation data (requires agent_id).
        db: The async database session.
        api_key: The validated API key.

    Returns:
        dict: The created session data.
    """
    session = SessionModel(
        agent_id=data.agent_id,
        conversation_id=data.conversation_id,
        status="active",
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)
    return SessionReadSchema.model_validate(session).model_dump()


@router.get("/sessions")
async def list_sessions(
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
    agent_id: uuid.UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List sessions with optional agent_id filter and pagination.

    Args:
        db: The async database session.
        api_key: The validated API key.
        agent_id: Optional filter by agent ID.
        page: Page number (1-indexed).
        page_size: Number of items per page.

    Returns:
        dict: ``{"items": [...], "total": int}`` with session list and total count.
    """
    base_query = select(SessionModel)
    if agent_id is not None:
        base_query = base_query.where(SessionModel.agent_id == agent_id)

    count_stmt = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    offset = (page - 1) * page_size
    stmt = base_query.order_by(SessionModel.created_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(stmt)
    sessions = result.scalars().all()

    return {
        "items": [SessionReadSchema.model_validate(s).model_dump() for s in sessions],
        "total": total,
    }


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """Get a session by ID.

    Args:
        session_id: The UUID of the session to retrieve.
        db: The async database session.
        api_key: The validated API key.

    Returns:
        dict: The session data.

    Raises:
        HTTPException: 404 if session not found.
    """
    result = await db.execute(select(SessionModel).where(SessionModel.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Session not found", "details": None}},
        )
    return SessionReadSchema.model_validate(session).model_dump()


@router.post("/sessions/{session_id}/resume")
async def resume_session(
    session_id: uuid.UUID,
    data: ResumeRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """Resume an interrupted session.

    Args:
        session_id: The UUID of the session to resume.
        data: The resume value (user input for interrupt).
        db: The async database session.
        api_key: The validated API key.

    Returns:
        dict: The session data after resume attempt.

    Raises:
        HTTPException: 404 if session not found, 400 if session is not interruptable.
    """
    result = await db.execute(select(SessionModel).where(SessionModel.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Session not found", "details": None}},
        )

    if session.status != "interrupted":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "INVALID_STATE",
                    "message": "Session is not in interrupted state",
                    "details": None,
                }
            },
        )

    session.status = "active"
    await db.flush()
    await db.refresh(session)
    return SessionReadSchema.model_validate(session).model_dump()
