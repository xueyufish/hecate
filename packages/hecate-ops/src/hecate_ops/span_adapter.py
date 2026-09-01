"""Shared OTel span adapter behind RuntimePort.create_span/end_span.

Both production RuntimePort implementations (``_ProductionRuntimePort`` and
``AgentExecutionPort``) previously carried byte-near-identical span methods.
They now delegate here.

Spans are tracked in a registry keyed by hex ``span_id`` so ``end_span``
resolves the exact span regardless of async interleaving — the previous
``trace.get_current_span()`` lookup could end the wrong span whenever sibling
spans overlapped on the same task context.
"""

from __future__ import annotations

import logging
from typing import Any

from hecate.engine.ports import SpanContext

logger = logging.getLogger(__name__)

# Upper bound guards against leaks from engine paths that create spans without
# a paired end; every well-behaved worker pairs create/end, so hitting the cap
# indicates a bug worth a warning, not a growing memory footprint.
_SPAN_REGISTRY_MAX = 1024

_active_spans: dict[str, Any] = {}


def reset_span_registry() -> None:
    """Clear the live-span registry. Test-only."""
    _active_spans.clear()


def create_otel_span(
    name: str,
    parent_id: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> SpanContext | None:
    """Start an OTel span and return its ID context.

    Args:
        name: Span name (engine convention prefixes: ``tool:``, ``llm:``,
            ``llm_stream:``, ``session:``).
        parent_id: Optional logical parent span ID (carried on SpanContext
            for engine bookkeeping; OTel parenting comes from the active
            context).
        attributes: Optional span attributes.

    Returns:
        ``SpanContext`` with hex span/trace IDs, or ``None`` when OTel is
        unavailable (degrade, never raise).
    """
    try:
        from opentelemetry import trace

        tracer = trace.get_tracer(__name__)
        span = tracer.start_span(name, attributes=attributes)
        ctx = span.get_span_context()
        span_id = format(ctx.span_id, "016x")
        if len(_active_spans) < _SPAN_REGISTRY_MAX:
            _active_spans[span_id] = span
        else:
            logger.warning("Span registry full (%d entries); '%s' cannot be ended by ID", _SPAN_REGISTRY_MAX, name)
        return SpanContext(
            span_id=span_id,
            trace_id=format(ctx.trace_id, "032x"),
            parent_id=parent_id,
        )
    except Exception:
        logger.debug("Tracing not available, returning None for span '%s'", name)
        return None


def end_otel_span(
    span_id: str,
    output_data: dict[str, Any] | None = None,
    usage: dict[str, int] | None = None,
) -> None:
    """End the span registered under ``span_id``.

    Looks the span up in the registry (not the ambient current span), writes
    ``output.*`` / ``usage.*`` attributes, then ends it. Unknown IDs (already
    ended, registry-capped, or OTel absent at create time) are a debug-level
    no-op so callers never need to guard.

    Args:
        span_id: Hex span ID returned by :func:`create_otel_span`.
        output_data: Optional output attributes (stringified).
        usage: Optional usage attributes (kept numeric).
    """
    span = _active_spans.pop(span_id, None)
    if span is None:
        logger.debug("Span %s not in registry (already ended or evicted)", span_id)
        return
    try:
        if output_data:
            for k, v in output_data.items():
                span.set_attribute(f"output.{k}", str(v))
        if usage:
            for k, v in usage.items():
                span.set_attribute(f"usage.{k}", v)
        span.end()
    except Exception:
        logger.debug("Failed to end span %s", span_id)


__all__ = ["create_otel_span", "end_otel_span", "reset_span_registry"]
