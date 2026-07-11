"""Tests for HecateTraceSpanProcessor — OTel-to-TraceModel bridge."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from hecate.services.observability.span_processor import (
    HecateTraceSpanProcessor,
    _build_metadata,
    _build_output_data,
    _build_usage,
    _infer_span_type,
    _otel_hex_to_uuid,
)


class TestInferSpanType:
    """Tests for span type inference from name prefix."""

    def test_tool_prefix(self) -> None:
        assert _infer_span_type("tool:get_weather") == "tool"

    def test_llm_prefix(self) -> None:
        assert _infer_span_type("llm:agent_node_1") == "generation"

    def test_llm_stream_prefix(self) -> None:
        assert _infer_span_type("llm_stream:agent_node_1") == "generation"

    def test_session_prefix(self) -> None:
        assert _infer_span_type("session:abc-123") == "trace"

    def test_unknown_prefix(self) -> None:
        assert _infer_span_type("custom_operation:data_sync") == "span"

    def test_empty_name(self) -> None:
        assert _infer_span_type("") == "span"


class TestOtelHexToUuid:
    """Tests for OTel hex to UUID conversion."""

    def test_32_char_hex(self) -> None:
        hex_str = "0af7651916cd43dd8448eb211c80319c"
        result = _otel_hex_to_uuid(hex_str)
        assert isinstance(result, uuid.UUID)
        assert str(result) == "0af76519-16cd-43dd-8448-eb211c80319c"

    def test_16_char_hex_padded(self) -> None:
        hex_str = "0c8a3b1234567890"
        result = _otel_hex_to_uuid(hex_str)
        assert isinstance(result, uuid.UUID)


class TestBuildMetadata:
    """Tests for OTel attribute extraction."""

    def test_metadata_with_attributes(self) -> None:
        span = MagicMock()
        span.get_span_context.return_value = MagicMock(
            trace_id=0x0AF7651916CD43DD8448EB211C80319C,
            span_id=0x0C8A3B1234567890,
        )
        span.attributes = {"tool_name": "get_weather", "model": "gpt-4o"}
        result = _build_metadata(span)
        assert "otel.trace_id" in result
        assert "otel.span_id" in result
        assert result["tool_name"] == "get_weather"
        assert result["model"] == "gpt-4o"

    def test_metadata_without_attributes(self) -> None:
        span = MagicMock()
        span.get_span_context.return_value = MagicMock(
            trace_id=0x0AF7651916CD43DD8448EB211C80319C,
            span_id=0x0C8A3B1234567890,
        )
        span.attributes = None
        result = _build_metadata(span)
        assert "otel.trace_id" in result
        assert "otel.span_id" in result


class TestBuildOutputData:
    """Tests for output attribute extraction."""

    def test_output_attributes(self) -> None:
        span = MagicMock()
        span.attributes = {"output.result_length": 42, "output.error": "timeout", "model": "gpt-4o"}
        result = _build_output_data(span)
        assert result == {"result_length": 42, "error": "timeout"}

    def test_no_output_attributes(self) -> None:
        span = MagicMock()
        span.attributes = {"model": "gpt-4o"}
        assert _build_output_data(span) is None

    def test_none_attributes(self) -> None:
        span = MagicMock()
        span.attributes = None
        assert _build_output_data(span) is None


class TestBuildUsage:
    """Tests for usage attribute extraction."""

    def test_usage_attributes(self) -> None:
        span = MagicMock()
        span.attributes = {"usage.input_tokens": 100, "usage.output_tokens": 50, "model": "gpt-4o"}
        result = _build_usage(span)
        assert result == {"input_tokens": 100, "output_tokens": 50}

    def test_non_int_usage_ignored(self) -> None:
        span = MagicMock()
        span.attributes = {"usage.input_tokens": "not_int"}
        assert _build_usage(span) is None


class TestSpanProcessorOnStart:
    """Tests for on_start — TraceModel creation."""

    async def test_on_start_creates_trace_model(self, db_session: AsyncMock) -> None:
        """on_start enqueues a TraceModel insert with correct fields."""
        processor = HecateTraceSpanProcessor(db_session_factory=lambda: db_session)
        processor._ensure_consumer = MagicMock()  # Don't start real consumer

        span = MagicMock()
        span.name = "tool:get_weather"
        span.get_span_context.return_value = MagicMock(
            trace_id=0x0AF7651916CD43DD8448EB211C80319C,
            span_id=0x0C8A3B1234567890,
        )
        span.parent = MagicMock(span_id=0x0C8A3B1234567890)
        span.attributes = {"tool_name": "get_weather", "session.id": str(uuid.uuid4())}

        processor.on_start(span)

        # Check queue has one item
        assert processor._queue.qsize() == 1
        item = processor._queue.get_nowait()
        assert item["op"] == "insert"
        assert item["data"]["type"] == "tool"
        assert item["data"]["name"] == "tool:get_weather"

    async def test_on_start_type_inference(self, db_session: AsyncMock) -> None:
        """on_start infers type from span name prefix."""
        processor = HecateTraceSpanProcessor(db_session_factory=lambda: db_session)
        processor._ensure_consumer = MagicMock()

        for name, expected_type in [
            ("tool:weather", "tool"),
            ("llm:node_1", "generation"),
            ("llm_stream:node_1", "generation"),
            ("session:abc", "trace"),
            ("custom:op", "span"),
        ]:
            span = MagicMock()
            span.name = name
            span.get_span_context.return_value = MagicMock(trace_id=1, span_id=2)
            span.parent = None
            span.attributes = {}

            processor.on_start(span)
            item = processor._queue.get_nowait()
            assert item["data"]["type"] == expected_type, f"Expected {expected_type} for {name}"


class TestSpanProcessorOnEnd:
    """Tests for on_end — TraceModel update."""

    async def test_on_end_updates_status(self, db_session: AsyncMock) -> None:
        """on_end enqueues an update with status, end_time, output_data."""
        processor = HecateTraceSpanProcessor(db_session_factory=lambda: db_session)
        processor._ensure_consumer = MagicMock()

        span = MagicMock()
        span.name = "tool:get_weather"
        span.status.status_code.name = "OK"
        span.attributes = {"output.result_length": 42}
        span._hecate_record_id = uuid.uuid4()

        processor.on_end(span)

        assert processor._queue.qsize() == 1
        item = processor._queue.get_nowait()
        assert item["op"] == "update"
        assert item["data"]["status"] == "completed"
        assert "end_time" in item["data"]

    async def test_on_end_error_status(self, db_session: AsyncMock) -> None:
        """on_end sets status to error when OTel status is ERROR."""
        processor = HecateTraceSpanProcessor(db_session_factory=lambda: db_session)
        processor._ensure_consumer = MagicMock()

        span = MagicMock()
        span.name = "tool:fail"
        span.status.status_code.name = "ERROR"
        span.attributes = {"output.error": "Connection refused"}
        span._hecate_record_id = uuid.uuid4()

        processor.on_end(span)

        item = processor._queue.get_nowait()
        assert item["data"]["status"] == "error"

    async def test_on_end_no_record_id(self, db_session: AsyncMock) -> None:
        """on_end does nothing if span has no _hecate_record_id."""
        processor = HecateTraceSpanProcessor(db_session_factory=lambda: db_session)
        processor._ensure_consumer = MagicMock()

        span = MagicMock()
        span.name = "tool:weather"
        span.status.status_code.name = "OK"
        span.attributes = {}
        # Explicitly remove _hecate_record_id to simulate no attribute
        del span._hecate_record_id

        processor.on_end(span)
        assert processor._queue.qsize() == 0


class TestQueueBehavior:
    """Tests for async queue and consumer."""

    async def test_queue_full_drops_span(self, db_session: AsyncMock) -> None:
        """When queue is full, spans are dropped with warning."""
        processor = HecateTraceSpanProcessor(db_session_factory=lambda: db_session)
        processor._ensure_consumer = MagicMock()
        processor._queue = asyncio.Queue(maxsize=1)  # Very small queue

        # Fill queue
        processor._queue.put_nowait({"op": "insert", "record_id": uuid.uuid4(), "data": {}})

        # Next one should be dropped
        span = MagicMock()
        span.name = "tool:test"
        span.get_span_context.return_value = MagicMock(trace_id=1, span_id=2)
        span.parent = None
        span.attributes = {}

        processor.on_start(span)
        # Queue still has only 1 item (the original)
        assert processor._queue.qsize() == 1

    async def test_force_flush_drains_queue(self, db_session: AsyncMock) -> None:
        """force_flush sends sentinel to stop consumer."""
        processor = HecateTraceSpanProcessor(db_session_factory=lambda: db_session)
        processor._consumer_task = MagicMock(done=MagicMock(return_value=False))

        processor.force_flush()

        # Sentinel should be in queue (the only item)
        sentinel = await processor._queue.get()
        assert sentinel is None


class TestOtelMetadataStorage:
    """Tests for OTel trace/span ID in metadata."""

    async def test_metadata_otel_ids(self, db_session: AsyncMock) -> None:
        """TraceModel metadata_ contains otel.trace_id and otel.span_id."""
        processor = HecateTraceSpanProcessor(db_session_factory=lambda: db_session)
        processor._ensure_consumer = MagicMock()

        span = MagicMock()
        span.name = "tool:test"
        span.get_span_context.return_value = MagicMock(
            trace_id=0x0AF7651916CD43DD8448EB211C80319C,
            span_id=0x0C8A3B1234567890,
        )
        span.parent = None
        span.attributes = {}

        processor.on_start(span)
        item = processor._queue.get_nowait()

        metadata = item["data"]["metadata_"]
        assert metadata["otel.trace_id"] == "0af7651916cd43dd8448eb211c80319c"
        assert metadata["otel.span_id"] == "0c8a3b1234567890"


class TestPregelRuntimeRootSpan:
    """Tests for PregelRuntime root span creation."""

    async def test_execute_with_otel_creates_root_span(self, db_session: AsyncMock) -> None:
        """execute() creates a root session span when OTel is available."""
        from hecate.engine.pregel import PregelRuntime
        from hecate.engine.types import CompiledGraph

        graph = MagicMock(spec=CompiledGraph)
        graph.name = "test"
        graph.entry_point = None
        graph.nodes = {}
        graph.edges = {}
        graph.channel_access = {}
        graph.channels = {}

        worker = MagicMock()
        checkpoint_store = MagicMock()
        checkpoint_store.load = AsyncMock(return_value=None)

        runtime = PregelRuntime(graph=graph, worker=worker, checkpoint_store=checkpoint_store)

        mock_span = MagicMock()
        mock_span.__enter__ = MagicMock(return_value=mock_span)
        mock_span.__exit__ = MagicMock(return_value=False)

        with patch("opentelemetry.trace.get_tracer") as mock_get_tracer:
            mock_tracer = MagicMock()
            mock_tracer.start_as_current_span.return_value = mock_span
            mock_get_tracer.return_value = mock_tracer

            session_id = uuid.uuid4()
            events = []
            async for event in runtime.execute(session_id=session_id):
                events.append(event)

            mock_tracer.start_as_current_span.assert_called_once()
            call_args = mock_tracer.start_as_current_span.call_args
            assert f"session:{session_id}" in call_args[0]

    async def test_execute_without_otel_runs_normally(self, db_session: AsyncMock) -> None:
        """execute() works normally when OTel is not available."""
        from hecate.engine.pregel import PregelRuntime
        from hecate.engine.types import CompiledGraph

        graph = MagicMock(spec=CompiledGraph)
        graph.name = "test"
        graph.entry_point = None
        graph.nodes = {}
        graph.edges = {}
        graph.channel_access = {}
        graph.channels = {}

        worker = MagicMock()
        checkpoint_store = MagicMock()
        checkpoint_store.load = AsyncMock(return_value=None)

        runtime = PregelRuntime(graph=graph, worker=worker, checkpoint_store=checkpoint_store)

        with patch("opentelemetry.trace.get_tracer", side_effect=ImportError("no otel")):
            session_id = uuid.uuid4()
            events = []
            async for event in runtime.execute(session_id=session_id):
                events.append(event)

            # Should complete without error
            assert isinstance(events, list)
