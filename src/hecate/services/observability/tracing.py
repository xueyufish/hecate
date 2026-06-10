"""Production tracing service backed by async SQLAlchemy.

Provides trace and span lifecycle management with persistence to the traces table.
Uses an observation-centric model where each record is self-referencing via parent_id.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.trace import TraceModel

logger = logging.getLogger(__name__)


class TracingService:
    """Production tracing service backed by the traces table.

    Provides methods to start/end traces and spans, list traces with filters,
    and retrieve trace details with span trees.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def start_trace(
        self,
        name: str,
        session_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        input_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TraceModel:
        """Start a new root trace and persist it."""
        trace_id = uuid.uuid4()
        record = TraceModel(
            trace_id=trace_id,
            parent_id=None,
            type="trace",
            name=name,
            session_id=session_id,
            agent_id=agent_id,
            user_id=user_id,
            input_data=input_data,
            metadata_=metadata or {},
            start_time=datetime.now(UTC),
        )
        self._db.add(record)
        await self._db.flush()
        return record

    async def start_span(
        self,
        trace_id: uuid.UUID,
        name: str,
        span_type: str = "span",
        parent_id: uuid.UUID | None = None,
        session_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        input_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TraceModel:
        """Start a new span within a trace and persist it."""
        record = TraceModel(
            trace_id=trace_id,
            parent_id=parent_id,
            type=span_type,
            name=name,
            session_id=session_id,
            agent_id=agent_id,
            user_id=user_id,
            input_data=input_data,
            metadata_=metadata or {},
            start_time=datetime.now(UTC),
        )
        self._db.add(record)
        await self._db.flush()
        return record

    async def end_span(
        self,
        record_id: uuid.UUID,
        output_data: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
        status: str = "completed",
    ) -> TraceModel | None:
        """End a span/trace by updating status, output, usage, and end_time."""
        result = await self._db.execute(
            select(TraceModel).where(TraceModel.id == record_id),
        )
        record = result.scalar_one_or_none()
        if record is None:
            return None

        record.output_data = output_data
        record.usage = usage
        record.status = status
        record.end_time = datetime.now(UTC)
        await self._db.flush()
        return record

    async def get_trace(self, trace_id: uuid.UUID) -> list[TraceModel]:
        """Get all records (root + spans) for a trace, ordered by start_time."""
        result = await self._db.execute(
            select(TraceModel).where(TraceModel.trace_id == trace_id).order_by(TraceModel.start_time),
        )
        return list(result.scalars().all())

    async def list_traces(
        self,
        session_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[TraceModel]:
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
        result = await self._db.execute(query)
        return list(result.scalars().all())
