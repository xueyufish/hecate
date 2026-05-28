"""Integration test for tracing service."""

from __future__ import annotations

from hecate.services.observability.tracing import TracingService


class TestTracingService:
    """Tests for the TracingService class."""

    def test_start_trace(self) -> None:
        """Test starting a new trace."""
        service = TracingService()

        context = service.start_trace(
            session_id="sess-1",
            agent_id="agent-1",
            user_id="user-1",
        )

        assert context.trace_id is not None
        assert context.session_id == "sess-1"
        assert context.agent_id == "agent-1"
        assert context.user_id == "user-1"

    def test_start_span(self) -> None:
        """Test starting a span within a trace."""
        service = TracingService()

        context = service.start_trace(session_id="sess-1")
        span = service.start_span(
            trace_id=context.trace_id,
            name="llm_call",
            input_data={"messages": []},
        )

        assert span.span_id is not None
        assert span.trace_id == context.trace_id
        assert span.name == "llm_call"

    def test_end_span(self) -> None:
        """Test ending a span."""
        service = TracingService()

        context = service.start_trace(session_id="sess-1")
        span = service.start_span(context.trace_id, "test")

        completed = service.end_span(
            span.span_id,
            output_data={"result": "ok"},
            usage={"input_tokens": 100},
        )

        assert completed is not None
        assert completed.end_time is not None
        assert completed.output_data == {"result": "ok"}
        assert completed.usage == {"input_tokens": 100}

    def test_end_span_not_found(self) -> None:
        """Test ending a non-existent span returns None."""
        service = TracingService()

        result = service.end_span("non-existent")
        assert result is None

    def test_record_cost(self) -> None:
        """Test recording cost attribution."""
        service = TracingService()

        context = service.start_trace(session_id="sess-1")
        service.record_cost(
            context.trace_id,
            {"total_tokens": 1000, "cost_usd": 0.05},
        )

        active = service.get_active_traces()
        assert len(active) == 1
        assert active[0].metadata["cost"]["cost_usd"] == 0.05

    def test_get_active_traces(self) -> None:
        """Test getting active traces."""
        service = TracingService()

        service.start_trace(session_id="s1")
        service.start_trace(session_id="s2")

        traces = service.get_active_traces()
        assert len(traces) == 2

    def test_get_active_spans(self) -> None:
        """Test getting active spans."""
        service = TracingService()

        context = service.start_trace()
        service.start_span(context.trace_id, "span1")
        service.start_span(context.trace_id, "span2")

        spans = service.get_active_spans()
        assert len(spans) == 2
