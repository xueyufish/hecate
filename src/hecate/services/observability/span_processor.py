"""OpenTelemetry SpanProcessor that bridges OTel spans to TraceModel.

Implements :class:`HecateTraceSpanProcessor` which intercepts span lifecycle
events (``on_start``, ``on_end``) and persists them to the ``traces`` table via
an async queue + background consumer pattern. This bridges the synchronous OTel
SDK with Hecate's async SQLAlchemy session.

Span type is inferred from the span name prefix:
- ``tool:`` → ``"tool"``
- ``llm:`` / ``llm_stream:`` → ``"generation"``
- ``session:`` → ``"trace"``
- Everything else → ``"span"``
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from opentelemetry import context
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor

from hecate.core.config import settings
from hecate.models.trace import TraceModel

logger = logging.getLogger(__name__)


def _infer_span_type(span_name: str) -> str:
    """Infer TraceModel.type from the OTel span name prefix.

    Args:
        span_name: The OTel span name (e.g., "tool:get_weather", "llm:node_1").

    Returns:
        One of "tool", "generation", "trace", or "span".
    """
    if span_name.startswith("tool:"):
        return "tool"
    if span_name.startswith(("llm:", "llm_stream:")):
        return "generation"
    if span_name.startswith("session:"):
        return "trace"
    return "span"


def _otel_hex_to_uuid(hex_str: str) -> uuid.UUID:
    """Convert an OTel hex string (32-char or 16-char) to UUID.

    For 32-char hex (trace_id), returns a direct UUID.
    For 16-char hex (span_id), pads to 32-char with leading zeros.

    Args:
        hex_str: Hex string from OTel span context.

    Returns:
        UUID representation.
    """
    padded = hex_str.rjust(32, "0")
    return uuid.UUID(padded)


def _build_metadata(span: ReadableSpan) -> dict[str, Any]:
    """Extract OTel attributes into a metadata dict.

    Includes OTel trace/span IDs for cross-referencing.

    Args:
        span: The OTel ReadableSpan.

    Returns:
        Dict with OTel attributes + otel.trace_id + otel.span_id.
    """
    ctx = span.get_span_context()
    metadata: dict[str, Any] = {
        "otel.trace_id": format(ctx.trace_id, "032x"),
        "otel.span_id": format(ctx.span_id, "016x"),
    }
    if span.attributes:
        for key, value in span.attributes.items():
            metadata[key] = value
    return metadata


def _build_output_data(span: ReadableSpan) -> dict[str, Any] | None:
    """Extract output-related attributes from a completed span.

    Attributes prefixed with ``output.`` are collected into output_data.

    Args:
        span: The completed OTel ReadableSpan.

    Returns:
        Dict of output attributes, or None if none present.
    """
    if not span.attributes:
        return None
    output: dict[str, Any] = {}
    for key, value in span.attributes.items():
        if key.startswith("output."):
            output[key.removeprefix("output.")] = value
    return output or None


def _build_usage(span: ReadableSpan) -> dict[str, int] | None:
    """Extract token usage attributes from a completed span.

    Attributes prefixed with ``usage.`` are collected into usage.

    Args:
        span: The completed OTel ReadableSpan.

    Returns:
        Dict of usage attributes, or None if none present.
    """
    if not span.attributes:
        return None
    usage: dict[str, int] = {}
    for key, value in span.attributes.items():
        if key.startswith("usage.") and isinstance(value, int):
            usage[key.removeprefix("usage.")] = value
    return usage or None


class HecateTraceSpanProcessor(SpanProcessor):
    """Bridges OTel spans to TraceModel via an async queue.

    On span start, enqueues a TraceModel record with start_time, name, type,
    and metadata. On span end, enqueues an update with end_time, status,
    output_data, and usage. A background consumer task batches writes to the
    database.

    The queue is bounded (``TRACE_DB_QUEUE_MAX_SIZE``). When full, new spans
    are silently dropped with a warning log to prevent unbounded memory growth.
    """

    def __init__(self, db_session_factory: Any = None) -> None:
        """Initialize the processor.

        Args:
            db_session_factory: An async callable that returns an AsyncSession.
                Defaults to ``async_session_factory`` from core.database.
        """
        self._db_session_factory = db_session_factory
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=settings.TRACE_DB_QUEUE_MAX_SIZE,
        )
        self._consumer_task: asyncio.Task[None] | None = None
        self._started = False

    def _ensure_consumer(self) -> None:
        """Start the background consumer if not already running."""
        if self._consumer_task is None or self._consumer_task.done():
            try:
                loop = asyncio.get_running_loop()
                self._consumer_task = loop.create_task(self._consumer_loop())
            except RuntimeError:
                logger.debug("No running event loop, consumer will start later")

    async def _consumer_loop(self) -> None:
        """Background task that drains the queue and writes to DB."""
        while True:
            batch: list[dict[str, Any]] = []
            try:
                item = await self._queue.get()
                if item is None:  # Shutdown sentinel
                    break
                batch.append(item)
                # Drain up to 50 more items without blocking
                for _ in range(50):
                    try:
                        item = self._queue.get_nowait()
                        if item is None:
                            break
                        batch.append(item)
                    except asyncio.QueueEmpty:
                        break
                await self._flush_batch(batch)
            except asyncio.CancelledError:
                # Flush remaining on cancellation
                if batch:
                    await self._flush_batch(batch)
                raise
            except Exception:
                logger.exception("Error in trace consumer loop")

    async def _flush_batch(self, batch: list[dict[str, Any]]) -> None:
        """Write a batch of span records to TraceModel.

        Args:
            batch: List of dicts with operation, trace_id, data fields.
        """
        if not batch:
            return

        if self._db_session_factory is None:
            from hecate.core.database import async_session_factory

            factory = async_session_factory
        else:
            factory = self._db_session_factory

        async with factory() as db:
            for item in batch:
                try:
                    op = item["op"]
                    if op == "insert":
                        record = TraceModel(**item["data"])
                        db.add(record)
                    elif op == "update":
                        from sqlalchemy import select

                        result = await db.execute(
                            select(TraceModel).where(
                                TraceModel.id == item["record_id"],
                            )
                        )
                        record = result.scalar_one_or_none()
                        if record:
                            for key, value in item["data"].items():
                                setattr(record, key, value)
                except Exception:
                    logger.exception("Failed to process trace record")
            await db.commit()

    def on_start(
        self,
        span: ReadableSpan,
        parent_context: context.Context | None = None,
    ) -> None:
        """Called when a span starts. Enqueues a TraceModel insert.

        Args:
            span: The span that started.
            parent_context: The parent OTel context (unused).
        """
        self._ensure_consumer()

        ctx = span.get_span_context()
        span_name = span.name
        span_type = _infer_span_type(span_name)
        trace_id = format(ctx.trace_id, "032x")

        # Extract parent span_id from OTel context
        parent_span_id: str | None = None
        if span.parent:
            parent_span_id = format(span.parent.span_id, "016x")

        metadata = _build_metadata(span)

        data = {
            "trace_id": _otel_hex_to_uuid(trace_id),
            "parent_id": _otel_hex_to_uuid(parent_span_id) if parent_span_id else None,
            "type": span_type,
            "name": span_name,
            "metadata_": metadata,
            "level": "DEFAULT",
            "status": "started",
            "start_time": datetime.now(UTC),
        }

        # Extract session_id and agent_id from attributes if present
        if span.attributes:
            if sid := span.attributes.get("session.id"):
                with contextlib.suppress(ValueError, AttributeError):
                    data["session_id"] = uuid.UUID(str(sid)) if not isinstance(sid, uuid.UUID) else sid
            if aid := span.attributes.get("agent.id"):
                with contextlib.suppress(ValueError, AttributeError):
                    data["agent_id"] = uuid.UUID(str(aid)) if not isinstance(aid, uuid.UUID) else aid

        record_id = uuid.uuid4()
        data["id"] = record_id

        try:
            self._queue.put_nowait({"op": "insert", "record_id": record_id, "data": data})
        except asyncio.QueueEmpty:
            pass
        except asyncio.QueueFull:
            logger.warning("Trace queue full, dropping span '%s'", span_name)

        # Store record_id on span for on_end lookup
        span._hecate_record_id = record_id

    def on_end(self, span: ReadableSpan) -> None:
        """Called when a span ends. Enqueues a TraceModel update.

        Args:
            span: The span that ended.
        """
        record_id = getattr(span, "_hecate_record_id", None)
        if record_id is None:
            return

        output_data = _build_output_data(span)
        usage = _build_usage(span)

        # Determine status from OTel status code
        status_code = span.status.status_code if span.status else None
        status = "error" if status_code and status_code.name == "ERROR" else "completed"

        update_data: dict[str, Any] = {
            "status": status,
            "end_time": datetime.now(UTC),
        }
        if output_data:
            update_data["output_data"] = output_data
        if usage:
            update_data["usage"] = usage

        try:
            self._queue.put_nowait({"op": "update", "record_id": record_id, "data": update_data})
        except asyncio.QueueFull:
            logger.warning("Trace queue full, dropping span update for '%s'", span.name)

    def shutdown(self) -> None:
        """Shutdown the processor. Flushes remaining spans."""
        self.force_flush()

    def force_flush(self, timeout_millis: float = 30000) -> bool:
        """Flush all pending spans from the queue.

        Args:
            timeout_millis: Timeout in milliseconds (unused, flush is immediate).

        Returns:
            True always.
        """
        if self._consumer_task and not self._consumer_task.done():
            try:
                self._queue.put_nowait(None)  # sentinel
            except asyncio.QueueFull:
                self._consumer_task.cancel()
        return True
