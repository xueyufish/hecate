"""Observability services for Hecate Agent platform.

This module provides observability capabilities:

- **otel_setup** — TracerProvider bootstrap; OTLP export when configured,
  console otherwise
- **span_processor** — bridges OTel spans to the TraceModel table and feeds
  MetricsStore counters/histograms
- **span_adapter** — shared OTel span adapter behind RuntimePort
  create_span/end_span
- **monitoring / metrics_storage** — real-time monitoring dashboards backed
  by pluggable metrics stores
- **replay companions** — logfold / loginvariants / logpolicy /
  orchestrator_validator (runtime-adjacent by design; never move to a wheel)
"""

from __future__ import annotations
