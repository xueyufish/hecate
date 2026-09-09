"""Session management API endpoints.

Provides operations for sessions:
- ``POST /api/sessions`` — Create a new session
- ``GET /api/sessions`` — List sessions (paginated, ``parent_session_id`` filter)
- ``GET /api/sessions/{id}`` — Get session by ID (with fork lineage)
- ``POST /api/sessions/{id}/resume`` — Resume an interrupted session
- ``GET /api/sessions/{id}/commit-points`` — List log-derived resume anchors
- ``POST /api/sessions/{id}/fork`` — Fork-and-run from a historical anchor
- ``POST /api/sessions/{id}/state`` — update_state (append-recorded mutation)
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_event_store import get_event_store
from hecate.core.deps_state_store import get_session_state_store
from hecate.core.deps_workspace import get_auth_context
from hecate.models.session import SessionCreateSchema, SessionModel, SessionReadSchema
from hecate.runtime.eventstore import EventStore
from hecate.runtime.session_state import SessionStateStore

router = APIRouter()


class ResumeRequest(BaseModel):
    """Request body for resuming an interrupted session."""

    resume_value: str


class ForkRequest(BaseModel):
    """Request body for forking a session from a historical commit point."""

    at_version: int = Field(ge=1)
    updates: dict[str, Any] | None = None
    model: str | None = None
    workflow_id: uuid.UUID | None = None
    tools: list[dict[str, Any]] | None = None
    kb_ids: list[str] | None = None


class StateUpdateRequest(BaseModel):
    """Request body for update_state (append-recorded state mutation)."""

    values: dict[str, Any]
    actor: str | None = None
    model: str | None = None
    workflow_id: uuid.UUID | None = None


def _build_time_travel_service(
    db: AsyncSession,
    event_store: EventStore,
    session_state_store: SessionStateStore | None,
) -> Any:
    """Wire a WorkflowExecutionService for commit-points/fork/state calls."""
    from hecate_llm.service import llm_service

    from hecate.core.composition.runtime_port_adapter import create_runtime_port
    from hecate.studio.workflows.execution_service import WorkflowExecutionService

    port = create_runtime_port(db, llm_service)
    return WorkflowExecutionService(port=port, db=db, event_store=event_store, checkpoint_store=session_state_store)


async def _require_session(db: AsyncSession, session_id: uuid.UUID, workspace_id: uuid.UUID | None) -> SessionModel:
    """Load a session row within the caller's workspace or raise 404."""
    conditions: list[Any] = [SessionModel.id == session_id]
    if workspace_id is not None:
        conditions.append(SessionModel.workspace_id == workspace_id)
    result = await db.execute(select(SessionModel).where(*conditions))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Session not found", "details": None}},
        )
    return session


def _session_payload(session: SessionModel) -> dict:
    """Read schema dump enriched with fork lineage from session metadata."""
    payload = SessionReadSchema.model_validate(session).model_dump()
    metadata = session.metadata_ or {}
    payload["parent_session_id"] = metadata.get("parent_session_id")
    payload["parent_log_version"] = metadata.get("parent_log_version")
    return payload


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
    parent_session_id: uuid.UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List sessions with optional agent_id / parent_session_id filters and pagination.

    Args:
        db: The async database session.
        ctx: The authenticated context with workspace_id.
        agent_id: Optional filter by agent ID.
        parent_session_id: Optional filter listing the what-if branches forked
            from this parent session.
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
    if parent_session_id is not None:
        base_query = base_query.where(SessionModel.metadata_["parent_session_id"].as_string() == str(parent_session_id))

    count_stmt = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    offset = (page - 1) * page_size
    stmt = base_query.order_by(SessionModel.created_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(stmt)
    sessions = result.scalars().all()

    return {
        "items": [_session_payload(s) for s in sessions],
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
    payload = _session_payload(session)
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
    from hecate.runtime.eventstore import EventType

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


@router.get("/sessions/{session_id}/commit-points")
async def list_commit_points(
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    event_store: Annotated[EventStore, Depends(get_event_store)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List log-derived resumable anchors (STEP_END / INTERRUPT / FORK), newest first.

    The anchor set comes purely from the event log — the checkpoint cache is
    never consulted, so results are identical with or without a warm cache.

    Raises:
        HTTPException: 404 if session not in the caller's workspace.
    """
    await _require_session(db, session_id, ctx.workspace_id)
    service = _build_time_travel_service(db, event_store, None)
    items = await service.list_commit_points(session_id, limit=limit)
    return {"items": items, "total": len(items)}


@router.post("/sessions/{session_id}/fork", status_code=status.HTTP_201_CREATED)
async def fork_session(
    session_id: uuid.UUID,
    data: ForkRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    event_store: Annotated[EventStore, Depends(get_event_store)],
    session_state_store: Annotated[SessionStateStore, Depends(get_session_state_store)],
) -> dict:
    """Fork-and-run: create a child session at a historical commit point and
    continue execution there with optional state updates.

    The parent session's log is never modified. Re-dispatched nodes re-execute
    tools and external side effects — effects from before the anchor are not
    rolled back (see the ``side_effects_note`` in the response).

    Raises:
        HTTPException: 404 if session not found; 422 for an unusable anchor
            (version beyond the log tail, below the first commit point, or an
            empty log).
    """
    from hecate.studio.workflows.execution_service import InvalidForkAnchorError

    await _require_session(db, session_id, ctx.workspace_id)
    service = _build_time_travel_service(db, event_store, session_state_store)
    try:
        result = await service.fork_session(
            session_id,
            at_version=data.at_version,
            updates=data.updates,
            actor=f"user:{ctx.user_id}" if ctx.user_id else "api",
            model=data.model,
            workflow_id=data.workflow_id,
            tools=data.tools,
            kb_ids=data.kb_ids,
            user_id=ctx.user_id,
        )
    except InvalidForkAnchorError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": {"code": "INVALID_FORK_ANCHOR", "message": str(exc), "details": None}},
        ) from exc
    return result


@router.post("/sessions/{session_id}/state")
async def update_session_state(
    session_id: uuid.UUID,
    data: StateUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    event_store: Annotated[EventStore, Depends(get_event_store)],
) -> dict:
    """update_state: append-recorded state mutation on a paused or idle session.

    Each channel update lands as one audited ``CHANNEL_WRITE`` event
    (``source="update_state"`` + actor) and applies through the channel's
    reducer semantics on the next fold. Refused with 409 while a turn is in
    flight (unclosed ``TURN_START`` without a following ``ERROR``).

    Raises:
        HTTPException: 404 if session not found; 409 while executing;
            422 for channels that are not loggable graph state.
    """
    from hecate.studio.workflows.execution_service import (
        InvalidStateChannelError,
        TurnInFlightError,
    )

    await _require_session(db, session_id, ctx.workspace_id)
    service = _build_time_travel_service(db, event_store, None)
    try:
        return await service.update_state(
            session_id,
            values=data.values,
            actor=data.actor or (f"user:{ctx.user_id}" if ctx.user_id else "api"),
            model=data.model,
            workflow_id=data.workflow_id,
        )
    except TurnInFlightError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "TURN_IN_FLIGHT", "message": str(exc), "details": None}},
        ) from exc
    except InvalidStateChannelError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": {"code": "INVALID_STATE_CHANNEL", "message": str(exc), "details": None}},
        ) from exc
