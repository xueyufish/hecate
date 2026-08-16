"""API router for trace querying endpoints.

Provides list and detail endpoints for traces with filtering support.
Traces are tenant-scoped: queries are filtered by the caller's workspace via
session_id/agent_id JOIN to SessionModel and AgentModel.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.database import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.agent import AgentModel
from hecate.models.session import SessionModel
from hecate.models.trace import TraceDetailSchema, TraceListSchema, TraceModel, TraceReadSchema

router = APIRouter()

_session_id_q = Query(default=None)
_agent_id_q = Query(default=None)
_start_time_q = Query(default=None)
_end_time_q = Query(default=None)
_limit_q = Query(default=20, ge=1, le=100)
_offset_q = Query(default=0, ge=0)


def _tenant_scoped_trace_ids_subquery(db_workspace_id: uuid.UUID) -> Any:
    """Build a subquery of trace_ids visible to ``workspace_id``.

    A trace is in-scope if its session belongs to the workspace, or its agent
    belongs to the workspace, or both. Orphan traces (no session/agent link)
    are excluded for non-admin callers.
    """
    session_subq = select(SessionModel.id).where(SessionModel.workspace_id == db_workspace_id)
    agent_subq = select(AgentModel.id).where(AgentModel.workspace_id == db_workspace_id)
    return session_subq, agent_subq


@router.get("/traces", response_model=list[TraceListSchema])
async def list_traces(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    session_id: uuid.UUID | None = _session_id_q,
    agent_id: uuid.UUID | None = _agent_id_q,
    start_time: datetime | None = _start_time_q,
    end_time: datetime | None = _end_time_q,
    limit: int = _limit_q,
    offset: int = _offset_q,
) -> Any:
    """List root trace records with optional filters. Tenant-scoped to caller's workspace."""
    session_subq, agent_subq = _tenant_scoped_trace_ids_subquery(ctx.workspace_id)

    query = select(TraceModel).where(
        TraceModel.parent_id.is_(None),
        or_(
            TraceModel.session_id.in_(session_subq),
            TraceModel.agent_id.in_(agent_subq),
        ),
    )
    if session_id is not None:
        query = query.where(TraceModel.session_id == session_id)
    if agent_id is not None:
        query = query.where(TraceModel.agent_id == agent_id)
    if start_time is not None:
        query = query.where(TraceModel.start_time >= start_time)
    if end_time is not None:
        query = query.where(TraceModel.start_time <= end_time)
    query = query.order_by(TraceModel.start_time.desc()).limit(limit).offset(offset)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/traces/{trace_id}", response_model=TraceDetailSchema)
async def get_trace(
    trace_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> Any:
    """Get trace detail with hierarchical span tree. Returns 404 for traces outside caller's workspace."""
    result = await db.execute(
        select(TraceModel).where(TraceModel.trace_id == trace_id).order_by(TraceModel.start_time),
    )
    records = list(result.scalars().all())
    if not records:
        raise HTTPException(status_code=404, detail="Trace not found")

    # Tenant check on the root record. If the root has neither session nor
    # agent in the caller's workspace, return 404 (not 403) to avoid leaking
    # trace existence.
    root = next((r for r in records if r.parent_id is None), records[0])
    if root.session_id is not None or root.agent_id is not None:
        session_subq, agent_subq = _tenant_scoped_trace_ids_subquery(ctx.workspace_id)
        conditions = []
        if root.session_id is not None:
            conditions.append(TraceModel.session_id.in_(session_subq))
        if root.agent_id is not None:
            conditions.append(TraceModel.agent_id.in_(agent_subq))
        if conditions:
            check = await db.execute(select(TraceModel.id).where(TraceModel.id == root.id, or_(*conditions)))
            if check.scalar_one_or_none() is None:
                raise HTTPException(status_code=404, detail="Trace not found")
    else:
        # Orphan trace (no session/agent) — hide from non-admin scopes.
        raise HTTPException(status_code=404, detail="Trace not found")

    spans = [r for r in records if r.parent_id is not None]
    return TraceDetailSchema(
        trace=TraceReadSchema.model_validate(root),
        spans=[TraceReadSchema.model_validate(s) for s in spans],
    )
