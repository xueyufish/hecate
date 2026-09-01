"""Tests for otel_setup — exporter selection and tracing bootstrap (PR3a)."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest
from hecate_ops import otel_setup

from hecate.core.config import settings


class TestParseOtlpHeaders:
    def test_basic_pairs(self) -> None:
        assert otel_setup.parse_otlp_headers("authorization=Bearer tok, x-key=1") == {
            "authorization": "Bearer tok",
            "x-key": "1",
        }

    def test_malformed_segments_skipped(self) -> None:
        assert otel_setup.parse_otlp_headers("a=1, junk, =2, b=3") == {"a": "1", "b": "3"}

    def test_empty_string(self) -> None:
        assert otel_setup.parse_otlp_headers("") == {}


class TestBuildSpanExporter:
    def test_console_when_unconfigured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter

        monkeypatch.setattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", "")
        assert isinstance(otel_setup.build_span_exporter(), ConsoleSpanExporter)

    def test_otlp_when_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        monkeypatch.setattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318/v1/traces")
        monkeypatch.setattr(settings, "OTEL_EXPORTER_OTLP_HEADERS", "authorization=Bearer tok")
        exporter = otel_setup.build_span_exporter()
        assert isinstance(exporter, OTLPSpanExporter)


class TestConfigureTracing:
    def test_disabled_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "TRACING_ENABLED", False)
        assert otel_setup.configure_tracing(MagicMock()) is None

    def test_missing_otel_packages_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A sys.modules None entry makes the OTel import raise ImportError."""
        monkeypatch.setattr(settings, "TRACING_ENABLED", True)
        monkeypatch.setitem(sys.modules, "opentelemetry.sdk.trace", None)
        assert otel_setup.configure_tracing(MagicMock()) is None

    def test_happy_path_returns_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fastapi import FastAPI
        from opentelemetry.sdk.trace import TracerProvider

        monkeypatch.setattr(settings, "TRACING_ENABLED", True)
        monkeypatch.setattr(settings, "TRACE_DB_EXPORT_ENABLED", False)
        # Keep the process-global provider untouched by this test.
        import opentelemetry.trace as otel_trace

        monkeypatch.setattr(otel_trace, "set_tracer_provider", lambda p: None)

        provider = otel_setup.configure_tracing(FastAPI())
        assert isinstance(provider, TracerProvider)
        provider.shutdown()  # stop the BatchSpanProcessor worker thread

    def test_sets_global_tracer_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_tracer() callers must be routed to the assembled provider.

        Regression guard: without set_tracer_provider, engine spans go to the
        global proxy no-op tracer and never reach any processor — only
        FastAPI HTTP spans (explicitly bound by instrument_app) were exported.
        """
        from fastapi import FastAPI

        monkeypatch.setattr(settings, "TRACING_ENABLED", True)
        monkeypatch.setattr(settings, "TRACE_DB_EXPORT_ENABLED", False)
        import opentelemetry.trace as otel_trace

        captured: dict[str, object] = {}
        monkeypatch.setattr(
            otel_trace,
            "set_tracer_provider",
            lambda p: captured.setdefault("provider", p),
        )

        provider = otel_setup.configure_tracing(FastAPI())
        assert provider is not None
        assert captured["provider"] is provider
        provider.shutdown()  # stop the BatchSpanProcessor worker thread
