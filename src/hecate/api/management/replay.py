"""Execution replay API endpoints (8.20).

Routes mounted under ``/sessions/{session_id}/replay``:

- ``GET /sessions/{id}/replay`` — trace-partitioned event timeline.
- ``GET /sessions/{id}/replay/state`` — time-travel state inspection.

The session detail endpoint ``GET /sessions/{id}`` is extended (in
``sessions.py``) with a ``log_version`` field.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_event_store import get_event_store
from hecate.core.deps_workspace import get_auth_context
from hecate.models.session import SessionModel
from hecate.runtime.eventstore import EventStore
from hecate.runtime.replay.logfold import NonReplayablePrefix
from hecate.services.replay.assembler import (
    REPLAY_PAYLOAD_PREVIEW_CHARS,
    assemble_timeline,
    derive_guardrail_blocks,
    derive_message_bodies,
    enrich_traces,
)
from hecate.services.replay.state_inspector import inspect_at_version

router = APIRouter()


async def _load_session_or_404(
    db: AsyncSession,
    session_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
) -> SessionModel:
    """Load a session scoped to caller's workspace; 404 on miss or cross-workspace."""
    conditions = [SessionModel.id == session_id]
    if workspace_id is not None:
        conditions.append(SessionModel.workspace_id == workspace_id)
    result = await db.execute(__import__("sqlalchemy", fromlist=["select"]).select(SessionModel).where(*conditions))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Session not found", "details": None}},
        )
    return session


@router.get("/sessions/{session_id}/replay")
async def get_replay_timeline(
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    event_store: Annotated[EventStore, Depends(get_event_store)],
    from_version: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    detail: Annotated[bool, Query()] = False,
) -> dict:
    """Trace-partitioned event timeline for a session.

    Args:
        session_id: Target session UUID.
        db: Async database session.
        ctx: Authenticated context (workspace scope).
        event_store: Wired EventStore singleton.
        from_version: Minimum event version (inclusive) for pagination.
        limit: Max events per page (1..500, default 100).
        detail: When True, return full payload (no truncation).

    Returns:
        Dict with ``traces`` (segmented timeline), ``unattributed`` events,
        ``next_cursor`` for pagination, ``guardrail_blocks``, ``message_bodies``,
        ``payload_truncated`` flag, ``payload_preview_chars``.

    Raises:
        HTTPException: 404 if session not in caller's workspace.
    """
    await _load_session_or_404(db, session_id, ctx.workspace_id)

    events = await event_store.get_events(session_id, from_version=from_version)
    timeline = assemble_timeline(events, detail=detail, from_version=from_version, limit=limit)

    trace_ids = [seg["trace_id"] for seg in timeline["traces"]]
    bodies = derive_message_bodies(events, trace_ids)
    guardrails = derive_guardrail_blocks(events)
    enrichment = await enrich_traces(events, db)

    return {
        **timeline,
        "guardrail_blocks": guardrails,
        "message_bodies": {f"{tid}::{eid}": msgs for (tid, eid), msgs in bodies.items()},
        "trace_enrichment": enrichment,
        "payload_preview_chars": REPLAY_PAYLOAD_PREVIEW_CHARS,
    }


@router.get("/sessions/{session_id}/replay/state")
async def get_replay_state(
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    event_store: Annotated[EventStore, Depends(get_event_store)],
    at_version: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    """Time-travel: fold log up to ``at_version`` and return state snapshot.

    Args:
        session_id: Target session UUID.
        db: Async database session.
        ctx: Authenticated context.
        event_store: Wired EventStore.
        at_version: Target version (will fall back to nearest commit point <=).

    Returns:
        Dict with ``effective_version``, ``channel_state``, ``messages``,
        ``commit_points``, ``fell_back`` flag.

    Raises:
        HTTPException: 404 if session not found; 422 if events below current
            ``log_schema_version`` are encountered.
    """
    await _load_session_or_404(db, session_id, ctx.workspace_id)

    events = await event_store.get_events(session_id)
    try:
        snapshot = inspect_at_version(events, at_version=at_version)
    except NonReplayablePrefix as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": {
                    "code": "NON_REPLAYABLE_PREFIX",
                    "message": "Event log contains events below current schema",
                    "details": {"session_id": str(e.session_id), "stopped_at_version": e.stopped_at_version},
                }
            },
        ) from e
    return snapshot
