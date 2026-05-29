"""Enhanced tracing service with LangFuse integration.

Provides Trace → Span → Generation hierarchy and cost attribution
for observability. Integrates with EvidenceTracker for tool call tracing.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TraceContext:
    """Context for a tracing session."""

    trace_id: str
    session_id: str | None = None
    agent_id: str | None = None
    user_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SpanData:
    """Data for a single span in a trace."""

    span_id: str
    trace_id: str
    name: str
    start_time: datetime
    end_time: datetime | None = None
    input_data: dict[str, Any] | None = None
    output_data: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)


class TracingService:
    """Enhanced tracing service with LangFuse integration.

    Provides:
    - Trace → Span → Generation hierarchy
    - Cost attribution per user, agent, session
    - Integration with EvidenceTracker
    """

    def __init__(self) -> None:
        """Initialize the tracing service."""
        self._active_traces: dict[str, TraceContext] = {}
        self._active_spans: dict[str, SpanData] = {}

    def start_trace(
        self,
        session_id: str | None = None,
        agent_id: str | None = None,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TraceContext:
        """Start a new trace.

        Args:
            session_id: Optional session identifier.
            agent_id: Optional agent identifier.
            user_id: Optional user identifier.
            metadata: Optional trace metadata.

        Returns:
            TraceContext with trace_id.
        """
        trace_id = str(uuid.uuid4())
        context = TraceContext(
            trace_id=trace_id,
            session_id=session_id,
            agent_id=agent_id,
            user_id=user_id,
            metadata=metadata or {},
        )
        self._active_traces[trace_id] = context

        logger.debug(f"Started trace {trace_id}")
        return context

    def start_span(
        self,
        trace_id: str,
        name: str,
        input_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SpanData:
        """Start a new span within a trace.

        Args:
            trace_id: Parent trace ID.
            name: Span name (e.g., "llm_call", "tool_execution").
            input_data: Optional input data.
            metadata: Optional span metadata.

        Returns:
            SpanData with span_id.
        """
        span_id = str(uuid.uuid4())
        span = SpanData(
            span_id=span_id,
            trace_id=trace_id,
            name=name,
            start_time=datetime.now(UTC),
            input_data=input_data,
            metadata=metadata or {},
        )
        self._active_spans[span_id] = span

        logger.debug(f"Started span {span_id} in trace {trace_id}")
        return span

    def end_span(
        self,
        span_id: str,
        output_data: dict[str, Any] | None = None,
        usage: dict[str, int] | None = None,
    ) -> SpanData | None:
        """End a span and record output.

        Args:
            span_id: Span to end.
            output_data: Optional output data.
            usage: Optional token usage data.

        Returns:
            Completed SpanData, or None if span not found.
        """
        span = self._active_spans.pop(span_id, None)
        if span is None:
            return None

        span.end_time = datetime.now(UTC)
        span.output_data = output_data
        span.usage = usage or {}

        logger.debug(f"Ended span {span_id}")
        return span

    def record_cost(
        self,
        trace_id: str,
        cost_data: dict[str, Any],
    ) -> None:
        """Record cost attribution data.

        Args:
            trace_id: Trace to record cost for.
            cost_data: Cost data (tokens, cost_usd, etc.).
        """
        context = self._active_traces.get(trace_id)
        if context:
            context.metadata["cost"] = cost_data
            logger.debug(f"Recorded cost for trace {trace_id}: {cost_data}")

    def record_evidence(
        self,
        trace_id: str,
        span_id: str | None = None,
        evidence_id: str | None = None,
        tool_name: str | None = None,
        is_error: bool = False,
    ) -> None:
        """Associate evidence capture with a trace.

        Args:
            trace_id: Trace to associate with.
            span_id: Optional span within the trace.
            evidence_id: Evidence record ID.
            tool_name: Tool that produced the evidence.
            is_error: Whether the evidence is an error.
        """
        context = self._active_traces.get(trace_id)
        if not context:
            return

        evidence_key = f"evidence_{evidence_id}" if evidence_id else f"evidence_{tool_name}"
        context.metadata[evidence_key] = {
            "evidence_id": evidence_id,
            "tool_name": tool_name,
            "is_error": is_error,
            "span_id": span_id,
        }

        if span_id:
            span = self._active_spans.get(span_id)
            if span:
                span.metadata["evidence_id"] = evidence_id

        logger.debug(f"Associated evidence {evidence_id} with trace {trace_id}")

    def get_active_traces(self) -> list[TraceContext]:
        """Get all active traces.

        Returns:
            List of active TraceContext.
        """
        return list(self._active_traces.values())

    def get_active_spans(self) -> list[SpanData]:
        """Get all active spans.

        Returns:
            List of active SpanData.
        """
        return list(self._active_spans.values())
