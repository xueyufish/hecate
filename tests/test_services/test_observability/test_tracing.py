"""Tests for TracingService backed by async SQLAlchemy."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from hecate.services.observability.tracing import TracingService


class TestTracingService:
    """Tests for the async TracingService class."""

    async def test_start_trace(self, db_session: AsyncSession) -> None:
        service = TracingService(db_session)
        session_id = uuid.uuid4()
        agent_id = uuid.uuid4()

        record = await service.start_trace(
            name="test-trace",
            session_id=session_id,
            agent_id=agent_id,
            input_data={"prompt": "hello"},
        )

        assert record.trace_id is not None
        assert record.parent_id is None
        assert record.type == "trace"
        assert record.name == "test-trace"
        assert record.session_id == session_id
        assert record.agent_id == agent_id
        assert record.status == "started"
        assert record.start_time is not None
        assert record.end_time is None

    async def test_start_span(self, db_session: AsyncSession) -> None:
        service = TracingService(db_session)
        trace = await service.start_trace(name="parent")

        span = await service.start_span(
            trace_id=trace.trace_id,
            name="llm_call",
            span_type="generation",
            input_data={"model": "gpt-4o"},
        )

        assert span.trace_id == trace.trace_id
        assert span.parent_id is None
        assert span.type == "generation"
        assert span.name == "llm_call"
        assert span.input_data == {"model": "gpt-4o"}
        assert span.status == "started"

    async def test_start_span_with_parent(self, db_session: AsyncSession) -> None:
        service = TracingService(db_session)
        trace = await service.start_trace(name="parent")
        parent_span = await service.start_span(trace_id=trace.trace_id, name="node_1")

        child = await service.start_span(
            trace_id=trace.trace_id,
            name="llm",
            parent_id=parent_span.id,
        )

        assert child.parent_id == parent_span.id
        assert child.trace_id == trace.trace_id

    async def test_end_span(self, db_session: AsyncSession) -> None:
        service = TracingService(db_session)
        trace = await service.start_trace(name="t")

        completed = await service.end_span(
            record_id=trace.id,
            output_data={"result": "ok"},
            usage={"input_tokens": 100, "output_tokens": 50},
        )

        assert completed is not None
        assert completed.end_time is not None
        assert completed.output_data == {"result": "ok"}
        assert completed.usage == {"input_tokens": 100, "output_tokens": 50}
        assert completed.status == "completed"

    async def test_end_span_with_error_status(self, db_session: AsyncSession) -> None:
        service = TracingService(db_session)
        trace = await service.start_trace(name="t")

        completed = await service.end_span(
            record_id=trace.id,
            output_data={"error": "timeout"},
            status="error",
        )

        assert completed is not None
        assert completed.status == "error"

    async def test_end_span_not_found(self, db_session: AsyncSession) -> None:
        service = TracingService(db_session)

        result = await service.end_span(record_id=uuid.uuid4())
        assert result is None

    async def test_get_trace(self, db_session: AsyncSession) -> None:
        service = TracingService(db_session)
        trace = await service.start_trace(name="root")
        await service.start_span(trace_id=trace.trace_id, name="span1")
        await service.start_span(trace_id=trace.trace_id, name="span2")

        records = await service.get_trace(trace.trace_id)

        assert len(records) == 3
        root = next(r for r in records if r.parent_id is None)
        spans = [r for r in records if r.parent_id is not None]
        assert root.name == "root"
        assert len(spans) == 0

    async def test_list_traces(self, db_session: AsyncSession) -> None:
        service = TracingService(db_session)
        session_a = uuid.uuid4()
        session_b = uuid.uuid4()
        await service.start_trace(name="t1", session_id=session_a)
        await service.start_trace(name="t2", session_id=session_b)

        all_traces = await service.list_traces()
        assert len(all_traces) == 2

        filtered = await service.list_traces(session_id=session_a)
        assert len(filtered) == 1
        assert filtered[0].name == "t1"

    async def test_list_traces_with_limit_offset(self, db_session: AsyncSession) -> None:
        service = TracingService(db_session)
        for i in range(5):
            await service.start_trace(name=f"trace-{i}")

        page1 = await service.list_traces(limit=2, offset=0)
        page2 = await service.list_traces(limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2

    async def test_full_trace_lifecycle(self, db_session: AsyncSession) -> None:
        service = TracingService(db_session)
        session_id = uuid.uuid4()
        agent_id = uuid.uuid4()

        trace = await service.start_trace(
            name="chat-session",
            session_id=session_id,
            agent_id=agent_id,
            input_data={"messages": [{"role": "user", "content": "hi"}]},
        )

        span = await service.start_span(
            trace_id=trace.trace_id,
            name="llm:gpt-4o",
            span_type="generation",
            input_data={"model": "gpt-4o", "tokens": 10},
        )

        await service.end_span(
            record_id=span.id,
            output_data={"response": "Hello!"},
            usage={"input_tokens": 10, "output_tokens": 5},
        )

        await service.end_span(
            record_id=trace.id,
            output_data={"status": "done"},
        )

        records = await service.get_trace(trace.trace_id)
        assert len(records) == 2

        root = next(r for r in records if r.type == "trace")
        child = next(r for r in records if r.type == "generation")
        assert root.name == "chat-session"
        assert root.status == "completed"
        assert child.name == "llm:gpt-4o"
        assert child.status == "completed"
        assert child.usage == {"input_tokens": 10, "output_tokens": 5}
