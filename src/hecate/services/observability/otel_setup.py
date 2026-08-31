"""OpenTelemetry bootstrap — single assembly point for the tracing pipeline.

Consolidates the TracerProvider wiring that used to live inline in ``main.py``
so the Phase 3b hecate-ops extraction becomes a file move. Exporter selection
is configuration-driven: an OTLP HTTP/protobuf exporter when
``OTEL_EXPORTER_OTLP_ENDPOINT`` is set, the console exporter otherwise (dev
default). The ``HecateTraceSpanProcessor`` DB bridge (and metrics feed) is
registered alongside when ``TRACE_DB_EXPORT_ENABLED``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from hecate.core.config import settings

if TYPE_CHECKING:
    from fastapi import FastAPI
    from opentelemetry.sdk.trace import TracerProvider

logger = logging.getLogger(__name__)


def parse_otlp_headers(raw: str) -> dict[str, str]:
    """Parse an OTel-style headers string into a dict.

    Follows the OTel convention of comma-separated ``k1=v1,k2=v2`` pairs.
    Malformed segments are skipped rather than raising so a typo cannot take
    down startup.

    Args:
        raw: The raw headers string (may be empty).

    Returns:
        Dict of header name to value.
    """
    headers: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, _, value = part.partition("=")
        if key.strip():
            headers[key.strip()] = value.strip()
    return headers


def build_span_exporter() -> Any:
    """Return the configured span exporter.

    OTLP HTTP/protobuf when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set (Langfuse,
    Tempo, Jaeger, Datadog OTLP ingest — all config-only), console exporter
    otherwise so dev installs keep seeing spans on stdout with zero setup.

    Returns:
        A ``SpanExporter`` instance.
    """
    if not settings.OTEL_EXPORTER_OTLP_ENDPOINT:
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter

        return ConsoleSpanExporter()

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    return OTLPSpanExporter(
        endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
        headers=parse_otlp_headers(settings.OTEL_EXPORTER_OTLP_HEADERS),
    )


def configure_tracing(app: FastAPI) -> TracerProvider | None:
    """Assemble and register the OTel tracing pipeline on the app.

    Args:
        app: The FastAPI application to instrument.

    Returns:
        The configured ``TracerProvider``, or ``None`` when tracing is
        disabled or the observability extras are not installed.
    """
    if not settings.TRACING_ENABLED:
        return None

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("OpenTelemetry packages not installed — tracing disabled (install with hecate[observability])")
        return None

    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(build_span_exporter()))

    if settings.TRACE_DB_EXPORT_ENABLED:
        from hecate.services.observability.monitoring import get_metrics_store
        from hecate.services.observability.span_processor import HecateTraceSpanProcessor

        trace_processor = HecateTraceSpanProcessor(metrics_store=get_metrics_store())
        provider.add_span_processor(trace_processor)
        trace_processor._ensure_consumer()

    # Route get_tracer() callers (engine, span_adapter) to this provider.
    # Without this, engine spans go to the global proxy no-op tracer and
    # never reach any processor — only FastAPI HTTP spans (explicitly bound
    # by instrument_app) were ever exported.
    from opentelemetry import trace

    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    logger.info(
        "Tracing configured (otlp_endpoint=%s, db_export=%s)",
        settings.OTEL_EXPORTER_OTLP_ENDPOINT or "<console>",
        settings.TRACE_DB_EXPORT_ENABLED,
    )
    return provider
