"""Production tracing service backed by async SQLAlchemy.

Provides trace and span lifecycle management with persistence to the traces table.
Uses an observation-centric model where each record is self-referencing via parent_id.
Optionally wires completed spans to a MetricsStore for real-time monitoring.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.trace import TraceModel

if TYPE_CHECKING:
    from hecate.engine.metrics_store import MetricsStore

logger = logging.getLogger(__name__)


class TracingService:
    """Production tracing service backed by the traces table.

    Provides methods to start/end traces and spans, list traces with filters,
    and retrieve trace details with span trees. When a MetricsStore is
    configured, completed spans automatically record metric counters for
    span count, token usage, and error rates.
    """

    def __init__(
        self,
        db: AsyncSession,
        metrics_store: MetricsStore | None = None,
    ) -> None:
        self._db = db
        self._metrics_store = metrics_store

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
        """End a span/trace by updating status, output, usage, and end_time.

        When a MetricsStore is configured, records metric counters for:
        - ``span.{type}.count`` — increment per completed span
        - ``span.{type}.duration_ms`` — span duration in milliseconds
        - ``tokens.input`` / ``tokens.output`` — token usage from usage dict
        - ``span.error.count`` — increment if status is "error"
        """
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

        if self._metrics_store is not None:
            self._record_span_metrics(record)

        return record

    def _record_span_metrics(self, record: TraceModel) -> None:
        """Record metrics for a completed span to the MetricsStore."""
        tags = {"type": record.type, "name": record.name}
        if record.agent_id:
            tags["agent_id"] = str(record.agent_id)

        self._metrics_store.record_counter(f"span.{record.type}.count", tags=tags)

        if record.start_time and record.end_time:
            duration_ms = (record.end_time - record.start_time).total_seconds() * 1000
            self._metrics_store.record_histogram(
                f"span.{record.type}.duration_ms",
                value=duration_ms,
                tags=tags,
            )

        if record.usage:
            if input_tokens := record.usage.get("input_tokens"):
                self._metrics_store.record_counter(
                    "tokens.input",
                    value=float(input_tokens),
                    tags=tags,
                )
            if output_tokens := record.usage.get("output_tokens"):
                self._metrics_store.record_counter(
                    "tokens.output",
                    value=float(output_tokens),
                    tags=tags,
                )

        if record.status == "error":
            self._metrics_store.record_counter("span.error.count", tags=tags)

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
