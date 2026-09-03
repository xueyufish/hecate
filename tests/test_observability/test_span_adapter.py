"""Tests for span_adapter — shared OTel span adapter behind RuntimePort (PR3a)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from hecate_ops import span_adapter
from hecate_ops.span_adapter import (
    create_otel_span,
    end_otel_span,
    reset_span_registry,
)

from hecate.runtime.ports import SpanContext


def _sdk_tracer() -> object:
    """A real SDK tracer from a throwaway provider (no global state)."""
    from opentelemetry.sdk.trace import TracerProvider

    return TracerProvider().get_tracer("test.span_adapter")


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    reset_span_registry()
    yield
    reset_span_registry()


class TestCreateOtelSpan:
    def test_returns_span_context_with_hex_ids(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("opentelemetry.trace.get_tracer", lambda *a, **k: _sdk_tracer())

        ctx = create_otel_span("tool:get_weather", parent_id="abc")

        assert isinstance(ctx, SpanContext)
        assert len(ctx.span_id) == 16
        assert len(ctx.trace_id) == 32
        int(ctx.span_id, 16)  # valid hex
        int(ctx.trace_id, 16)
        assert ctx.parent_id == "abc"

    def test_registers_span_for_end_lookup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("opentelemetry.trace.get_tracer", lambda *a, **k: _sdk_tracer())

        ctx = create_otel_span("llm:node_1")

        assert ctx is not None
        assert ctx.span_id in span_adapter._active_spans

    def test_returns_none_when_otel_unavailable(self) -> None:
        with patch("opentelemetry.trace.get_tracer", side_effect=ImportError("no otel")):
            assert create_otel_span("tool:x") is None


class TestEndOtelSpan:
    def test_ends_registered_span_with_attributes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("opentelemetry.trace.get_tracer", lambda *a, **k: _sdk_tracer())

        ctx = create_otel_span("tool:get_weather")
        assert ctx is not None
        span = span_adapter._active_spans[ctx.span_id]

        end_otel_span(
            ctx.span_id,
            output_data={"result_length": 42},
            usage={"input_tokens": 100},
        )

        assert ctx.span_id not in span_adapter._active_spans
        assert span.end_time is not None  # SDK span recorded an end
        assert span.attributes["output.result_length"] == "42"
        assert span.attributes["usage.input_tokens"] == 100

    def test_double_end_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("opentelemetry.trace.get_tracer", lambda *a, **k: _sdk_tracer())

        ctx = create_otel_span("tool:x")
        assert ctx is not None
        end_otel_span(ctx.span_id)
        end_otel_span(ctx.span_id)  # must not raise

    def test_unknown_span_id_is_noop(self) -> None:
        end_otel_span("deadbeefdeadbeef")  # must not raise

    def test_registry_cap_evicts_no_end(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("opentelemetry.trace.get_tracer", lambda *a, **k: _sdk_tracer())
        monkeypatch.setattr(span_adapter, "_SPAN_REGISTRY_MAX", 1)

        first = create_otel_span("tool:first")
        second = create_otel_span("tool:second")

        assert first is not None
        assert second is not None
        assert first.span_id in span_adapter._active_spans
        assert second.span_id not in span_adapter._active_spans
        end_otel_span(second.span_id)  # no-op, must not raise


class TestPortDelegation:
    async def test_runtime_port_methods_delegate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Both RuntimePort implementations share the adapter behavior."""
        from hecate.core.composition.runtime_port_adapter import _ProductionRuntimePort
        from hecate.runtime.agent_execution_port import AgentExecutionPort

        monkeypatch.setattr("opentelemetry.trace.get_tracer", lambda *a, **k: _sdk_tracer())

        for port in (
            _ProductionRuntimePort(db=None, llm_service=None),  # type: ignore[arg-type]
            AgentExecutionPort(db=None),  # type: ignore[arg-type]
        ):
            ctx = await port.create_span("tool:delegation_check")
            assert isinstance(ctx, SpanContext)
            assert ctx is not None
            assert await port.end_span(ctx.span_id, output_data={"ok": True}) is None
