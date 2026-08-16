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

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_event_store import get_event_store
from hecate.core.deps_state_store import get_session_state_store
from hecate.core.deps_workspace import get_auth_context
from hecate.engine.eventstore import EventStore
from hecate.engine.session_state import SessionStateStore
from hecate.models.session import SessionCreateSchema, SessionModel, SessionReadSchema

router = APIRouter()


class ResumeRequest(BaseModel):
    """Request body for resuming an interrupted session."""

    resume_value: str


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    data: SessionCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Create a new session.

    Args:
        data: The session creation data (requires agent_id).
        db: The async database session.
        ctx: The authenticated context with workspace_id.

    Returns:
        dict: The created session data.
    """
    session = SessionModel(
        agent_id=data.agent_id,
        conversation_id=data.conversation_id,
        status="active",
        workspace_id=ctx.workspace_id or uuid.UUID(int=0),
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)
    return SessionReadSchema.model_validate(session).model_dump()


@router.get("/sessions")
async def list_sessions(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    agent_id: uuid.UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List sessions with optional agent_id filter and pagination.

    Args:
        db: The async database session.
        ctx: The authenticated context with workspace_id.
        agent_id: Optional filter by agent ID.
        page: Page number (1-indexed).
        page_size: Number of items per page.

    Returns:
        dict: ``{"items": [...], "total": int}`` with session list and total count.
    """
    base_query = select(SessionModel)
    if ctx.workspace_id is not None:
        base_query = base_query.where(SessionModel.workspace_id == ctx.workspace_id)
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
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    event_store: Annotated[EventStore, Depends(get_event_store)],
) -> dict:
    """Get a session by ID.

    Args:
        session_id: The UUID of the session to retrieve.
        db: The async database session.
        ctx: The authenticated context with workspace_id.
        event_store: Wired EventStore; used to expose ``log_version`` so the
            frontend can decide whether to render the execution replay tab.

    Returns:
        dict: The session data plus ``log_version`` (int; 0 = no event log).

    Raises:
        HTTPException: 404 if session not found.
    """
    conditions = [SessionModel.id == session_id]
    if ctx.workspace_id is not None:
        conditions.append(SessionModel.workspace_id == ctx.workspace_id)
    result = await db.execute(select(SessionModel).where(*conditions))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Session not found", "details": None}},
        )
    payload = SessionReadSchema.model_validate(session).model_dump()
    payload["log_version"] = await event_store.get_version(session_id)
    return payload


@router.post("/sessions/{session_id}/resume")
async def resume_session(
    session_id: uuid.UUID,
    data: ResumeRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    event_store: Annotated[EventStore, Depends(get_event_store)],
    session_state_store: Annotated[SessionStateStore, Depends(get_session_state_store)],
) -> dict:
    """Resume an interrupted session.

    Per the 1.3.19 log-as-truth change the resume validation is log-derived:
    the endpoint reads the session's events and confirms an unclosed INTERRUPT
    event is present before accepting the resume. The SessionModel row is
    lazy-created if absent (chat API path B never persists SessionModel).

    Args:
        session_id: The UUID of the session to resume.
        data: The resume value (user input for interrupt).
        db: The database session.
        ctx: The authenticated context with workspace_id.
        event_store: Wired EventStore (injected via Depends).
        session_state_store: Wired SessionStateStore (for cache invalidation).

    Returns:
        dict: The session data after resume attempt.

    Raises:
        HTTPException: 400 if no unclosed INTERRUPT exists in the event log.
    """
    from hecate.engine.eventstore import EventType

    events = await event_store.get_events(session_id)
    has_unclosed_interrupt = any(event.event_type == EventType.INTERRUPT for event in events) and not any(
        event.event_type == EventType.RESUME
        for event in events[[i for i, e in enumerate(events) if e.event_type == EventType.INTERRUPT][-1] + 1 :]
    )
    if not has_unclosed_interrupt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "NOT_INTERRUPTED",
                    "message": "Session has no unclosed INTERRUPT event in the log",
                    "details": None,
                }
            },
        )

    conditions = [SessionModel.id == session_id]
    if ctx.workspace_id is not None:
        conditions.append(SessionModel.workspace_id == ctx.workspace_id)
    result = await db.execute(select(SessionModel).where(*conditions))
    session = result.scalar_one_or_none()
    if session is None:
        session = SessionModel(
            id=session_id,
            agent_id=uuid.UUID(int=0),
            status="active",
            workspace_id=ctx.workspace_id or uuid.UUID(int=0),
        )
        db.add(session)
    else:
        if session.status != "interrupted":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": "INVALID_STATE",
                        "message": "Session row not in interrupted state; will be reconciled via log",
                        "details": None,
                    }
                },
            )
        session.status = "active"
    await db.flush()
    await db.refresh(session)
    return SessionReadSchema.model_validate(session).model_dump()
