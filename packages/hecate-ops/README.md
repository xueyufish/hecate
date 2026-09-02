# hecate-ops

Hecate's observability pipeline domain, extracted from the core package as
part of the package-split plan (PR3b).

## Contents

- **otel_setup** — TracerProvider bootstrap; OTLP HTTP/protobuf export when
  `OTEL_EXPORTER_OTLP_ENDPOINT` is set, console exporter otherwise. Sets the
  global tracer provider so engine-side `get_tracer()` spans flow end-to-end.
- **span_processor** — bridges OTel spans to the core `TraceModel` table via
  a bounded async queue, and feeds the MetricsStore with per-type counters,
  duration histograms, error and token totals.
- **span_adapter** — the shared OTel span implementation behind
  `RuntimePort.create_span` / `end_span` (span-id registry, async-safe).
- **monitoring / metrics_storage / timescale_metrics_store** — the
  application MetricsStore singleton, pluggable backends (in-memory ring
  buffers, TimescaleDB), and the WebSocket push service behind
  `/ws/monitoring` and `/api/monitoring/*`.
- **api/monitoring** — the monitoring routes (moved wholesale from the core
  `api/management/` in keeping with the hecate-memory precedent).
- **metrics** — in-process Prometheus-compatible collector behind the
  core `/metrics` endpoint.

## Relationship to core

`hecate-ops` is a **required** dependency of the core `hecate` package:
tracing and monitoring are platform capabilities, not optional extras. The
core's orchestration adapters import `hecate_ops.span_adapter` directly, and
the main application mounts `hecate_ops.api.monitoring` lazily (the guard
exists for test isolation, not for optional installation).

Engine-sufficiency note: `hecate.runtime.*` never imports `hecate_ops` — the
engine's observability companions (`logfold`, `loginvariants*`, `logpolicy`,
`orchestrator_validator`) deliberately remain in the core package and are
guarded by `tests/test_engine/test_runtime_self_sufficiency.py`.

## Install

```bash
# As part of the uv workspace (recommended for development)
uv sync --package hecate --package hecate-ops --extra dev --prerelease=allow

# OpenTelemetry runtime (OTLP export, FastAPI instrumentation)
pip install 'hecate-ops[otel]'
```
