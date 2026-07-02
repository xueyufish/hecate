## Context

Hecate completed 8.1 Full-Chain Tracing, which writes trace/span records to a `traces` table in PostgreSQL via `TracingService`. An in-memory `MetricsCollector` exists (`services/observability/metrics.py`) but has no time-windowed aggregation, no percentile calculations, and is not exposed via any API. No WebSocket or SSE endpoints exist outside of the OpenAI-compatible chat streaming in `api/v1/chat.py`.

## Goals / Non-Goals

**Goals:**
- Provide real-time metric snapshots (error rate, latency percentiles, active sessions, token usage) via WebSocket push every 5 seconds
- Provide on-demand metric queries via REST API with configurable time windows
- Support dual-mode storage: in-memory (default, zero deps) and TimescaleDB (optional, for production persistence)
- Automatically update metrics from trace completion — no additional instrumentation needed beyond what 8.1 already provides
- Follow existing Hecate patterns (ABC in engine/, implementations in services/, config-based factory selection)

**Non-Goals:**
- Building a frontend dashboard UI (this is backend-only)
- Supporting alerting/thresholds (that is feature 8.6, depends on this)
- Supporting ClickHouse or other TSDB backends (only TimescaleDB and in-memory for now)
- Persisting in-memory metrics across server restarts
- Supporting per-user/per-workspace metric isolation in this iteration

## Decisions

### D1: WebSocket over SSE for dashboard streaming

**Decision**: Use FastAPI native WebSocket (`/ws/monitoring`) for real-time metric push.

**Rationale**: Grafana (Centrifuge) and Datadog both use WebSocket for dashboard streaming. WebSocket supports bidirectional communication (client can subscribe/unsubscribe to specific metric channels) and multiplexed subscriptions. FastAPI >= 0.115 has native WebSocket support — zero additional dependencies.

**Alternatives considered**:
- SSE: Simpler but unidirectional; no multiplexed subscriptions; auto-reconnect is the only advantage, but WebSocket clients handle this equally well
- Polling: Works but 30-60s latency is insufficient for real-time debugging

### D2: MetricsStore ABC with dual implementation (InMemory + TimescaleDB)

**Decision**: Define `MetricsStore` ABC in `engine/metrics_store.py` with `InMemoryMetricsStore` (same file, zero deps) and `TimescaleMetricsStore` in `services/observability/timescale_metrics_store.py`. Config flag `METRICS_STORE_TYPE` selects implementation via factory function.

**Rationale**: Follows the exact pattern used by `CheckpointStore` (engine/ ABC + InMemory) and `PostgresCheckpointStore` (services/ impl), and `VectorStore` (config-based factory). InMemory for dev/test, TimescaleDB for production.

**Alternatives considered**:
- Only in-memory: Loses historical data, can't query trends
- Only TimescaleDB: Adds hard dependency on extension, breaks dev/test
- ClickHouse: Overkill for this stage, would add a new service dependency

### D3: Sliding window aggregation with configurable time buckets

**Decision**: InMemoryMetricsStore maintains ring buffers for fixed time windows (1m, 5m, 15m, 1h). Each metric event appends to all applicable windows. Windows expire and are garbage-collected.

**Rationale**: Ring buffers provide O(1) append and O(n) scan where n is the number of events in the window. For 5-second snapshots at 1000 req/s, the 1m window holds at most 60K entries — well within memory budget. TimescaleMetricsStore uses SQL `time_bucket()` for server-side aggregation.

### D4: 5-second push interval via background task

**Decision**: `MonitoringService` runs an `asyncio.Task` that queries MetricsStore every 5 seconds and broadcasts the snapshot to all connected WebSocket clients.

**Rationale**: Datadog live mode uses 2s intervals but limits to 50 hosts. Grafana uses real-time push on data change. 5s is the balance between freshness and database/Network load. For in-memory store, the query is O(1) — no concern. For TimescaleDB, the query hits a pre-aggregated materialized view.

### D5: Metrics updated on trace/span completion only

**Decision**: Wire into `TracingService.end_span()` to call `MetricsStore.record_*()`. No changes to hot paths (LLM invocation, tool execution) — only the completion path.

**Rationale**: Minimizes performance impact. Trace completion is already an async DB write; adding an in-memory counter increment is negligible. This avoids touching `PregelRuntime`, `LLMWorker`, or `ToolWorker` — all metrics come from the existing trace data.

### D6: WebSocket connection manager with graceful shutdown

**Decision**: `ConnectionManager` class maintains a `set[WebSocket]` of active connections. Broadcast iterates over a frozen copy, catches `WebSocketDisconnect` per-client, and removes stale connections. On application shutdown, sends a final `{\"type\": \"shutdown\"}` message and closes all connections.

**Rationale**: Standard pattern used in FastAPI documentation. Frozen copy prevents mutation during iteration. Per-client error handling prevents one broken connection from blocking the entire broadcast.

## Risks / Trade-offs

| Risk | Impact | Mitigation |
|------|--------|------------|
| InMemoryMetricsStore loses data on restart | Medium | Document limitation; TimescaleDB mode for production persistence |
| TimescaleDB extension not installed | Low | Graceful fallback to in-memory; `METRICS_STORE_TYPE` default is `"in_memory"` |
| WebSocket connection scaling (1000+ concurrent dashboards) | Low for now | In-memory broadcast is O(n) connections; for scale, add Redis pub/sub backplane later |
| 5-second push interval may feel sluggish | Low | Configurable via `METRICS_PUSH_INTERVAL`; can reduce to 2s if needed |
| Ring buffer memory usage under high load | Low | 1m window at 10K req/s = 600K entries ≈ 50MB; add `MAX_METRICS_BUFFER_SIZE` cap |
