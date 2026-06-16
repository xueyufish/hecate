## Why

Hecate has full-chain tracing (8.1) recording every trace and span to PostgreSQL, but operators have no way to observe system health in real time. Without live visibility into error rates, latency percentiles, active sessions, and token consumption, production incidents go undetected until users complain. Industry-standard platforms (Grafana, Datadog, LangFuse) all provide real-time monitoring dashboards — Hecate needs one too.

## What Changes

- Add a **MetricsStore ABC** in the engine layer with two implementations: `InMemoryMetricsStore` (default, zero dependencies) and `TimescaleMetricsStore` (optional, uses PostgreSQL `time_bucket` when TimescaleDB extension is available). Follows the existing CheckpointStore dual-implementation pattern.
- Enhance the existing `MetricsCollector` with **time-windowed aggregation** (sliding windows for last 1m/5m/15m/1h), **percentile latency** (p50/p95/p99), and **per-dimension breakdowns** (by agent, model, session).
- Add a **WebSocket endpoint** (`/ws/monitoring`) that pushes aggregated metric snapshots to connected dashboard clients every 5 seconds, using FastAPI's built-in WebSocket support with a `ConnectionManager` for multi-client broadcast.
- Add a **REST API** (`GET /api/monitoring/metrics`) for on-demand metric queries with configurable time windows and dimensions, serving clients that prefer polling over WebSocket.
- Wire `MetricsCollector` into the trace completion path so that every `end_span` call automatically updates counters, histograms, and gauges without additional instrumentation.
- Add `METRICS_STORE_TYPE` config flag (`"in_memory"` | `"timescale"`) and factory function following the `VECTOR_STORE_TYPE` pattern.

## Capabilities

### New Capabilities
- `real-time-monitoring`: WebSocket-based real-time metric streaming, time-windowed aggregation (InMemory + TimescaleDB), REST metric query API, and connection management for dashboard clients

### Modified Capabilities
- `core-infrastructure`: Add `METRICS_STORE_TYPE` and `METRICS_PUSH_INTERVAL` settings to core config; add monitoring WebSocket route and REST endpoint registration in main.py
- `full-chain-tracing`: Wire TracingService.end_span to update MetricsStore counters and histograms on every span completion

## Impact

- **Engine layer**: New `MetricsStore` ABC + `InMemoryMetricsStore` in `engine/metrics_store.py` (zero external deps)
- **Services layer**: New `MonitoringService` in `services/observability/monitoring.py`; `TimescaleMetricsStore` in `services/observability/timescale_metrics_store.py`; enhanced `MetricsCollector` with time windows
- **API layer**: New WebSocket endpoint + REST endpoint in `api/management/monitoring.py`; route registration in `main.py`
- **Config**: Two new settings in `core/config.py`
- **Dependencies**: No new required dependencies. Optional: TimescaleDB PostgreSQL extension for production TSDB mode
- **Tests**: New test file for MetricsStore ABC, InMemoryMetricsStore, MonitoringService, and WebSocket endpoint
