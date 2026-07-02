"""API router for trace querying endpoints.

Provides list and detail endpoints for traces with filtering support.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.database import get_db
from hecate.models.trace import TraceDetailSchema, TraceListSchema, TraceModel, TraceReadSchema

router = APIRouter()

_session_id_q = Query(default=None)
_agent_id_q = Query(default=None)
_start_time_q = Query(default=None)
_end_time_q = Query(default=None)
_limit_q = Query(default=20, ge=1, le=100)
_offset_q = Query(default=0, ge=0)
_db_dep = Depends(get_db)


@router.get("/traces", response_model=list[TraceListSchema])
async def list_traces(
    session_id: uuid.UUID | None = _session_id_q,
    agent_id: uuid.UUID | None = _agent_id_q,
    start_time: datetime | None = _start_time_q,
    end_time: datetime | None = _end_time_q,
    limit: int = _limit_q,
    offset: int = _offset_q,
    db: AsyncSession = _db_dep,
) -> Any:
    """List root trace records with optional filters."""
    query = select(TraceModel).where(TraceModel.parent_id.is_(None))
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
    db: AsyncSession = _db_dep,
) -> Any:
    """Get trace detail with hierarchical span tree."""
    result = await db.execute(
        select(TraceModel).where(TraceModel.trace_id == trace_id).order_by(TraceModel.start_time),
    )
    records = list(result.scalars().all())
    if not records:
        raise HTTPException(status_code=404, detail="Trace not found")

    root = next((r for r in records if r.parent_id is None), records[0])
    spans = [r for r in records if r.parent_id is not None]
    return TraceDetailSchema(
        trace=TraceReadSchema.model_validate(root),
        spans=[TraceReadSchema.model_validate(s) for s in spans],
    )
