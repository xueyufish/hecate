## 1. Configuration and Data Model

- [x] 1.1 Add `METRICS_STORE_TYPE` (default `"in_memory"`, values `"in_memory"` | `"timescale"`), `METRICS_PUSH_INTERVAL` (default `5`), and `MAX_METRICS_BUFFER_SIZE` (default `100000`) settings to `src/hecate/core/config.py`
- [x] 1.2 Create `MetricsModel` ORM model in `src/hecate/models/metric.py` with fields: `id`, `timestamp`, `name`, `kind` (counter/gauge/histogram), `value` (float), `tags` (JSONB); with composite index on `(name, timestamp)` and TimescaleDB hypertable hint in docstring
- [x] 1.3 Add `MetricsModel` to `src/hecate/models/__init__.py` exports and import in `tests/conftest.py` for table registration

## 2. Engine Layer — MetricsStore ABC

- [x] 2.1 Create `MetricsStore` ABC in `src/hecate/engine/metrics_store.py` with abstract methods: `record_counter(name, value, tags)`, `record_gauge(name, value, tags)`, `record_histogram(name, value, tags)`, `query_metrics(name, window, aggregation, tags)`, `get_snapshot(windows) -> MetricsSnapshot`
- [x] 2.2 Define `MetricsSnapshot` Pydantic model with fields: `timestamp` (datetime), `windows` (dict of window name to metric dict), `counters`, `gauges`, `histograms`
- [x] 2.3 Implement `InMemoryMetricsStore` in the same file using ring buffers organized by time window (1m, 5m, 15m, 1h) with garbage collection on snapshot queries and `MAX_METRICS_BUFFER_SIZE` cap

## 3. Service Layer — MonitoringService

- [x] 3.1 Create `ConnectionManager` class in `src/hecate/services/observability/monitoring.py` with methods: `connect(websocket)`, `disconnect(websocket)`, `broadcast(message)`, `shutdown()`; iterate frozen copy on broadcast, catch per-client disconnects
- [x] 3.2 Create `MonitoringService` class with methods: `start()`, `stop()`, `get_metrics_store()`, `_push_loop()` (async background task querying MetricsStore.get_snapshot and broadcasting via ConnectionManager every METRICS_PUSH_INTERVAL seconds)
- [x] 3.3 Implement `TimescaleMetricsStore` in `src/hecate/services/observability/timescale_metrics_store.py` with `async_sessionmaker` for self-managed transactions, `time_bucket()` for aggregation with `date_trunc()` fallback when TimescaleDB is not installed
- [x] 3.4 Create `get_metrics_store()` factory function in `src/hecate/services/observability/monitoring.py` using `METRICS_STORE_TYPE` config with match/case, importing concrete implementations inside case blocks

## 4. API Layer

- [x] 4.1 Create WebSocket endpoint `ws_monitoring()` in `src/hecate/api/management/monitoring.py` at path `/ws/monitoring` that accepts connections, handles subscribe/ping actions, and sends periodic snapshots from MonitoringService
- [x] 4.2 Create REST endpoint `get_metrics()` in the same file at `GET /api/monitoring/metrics` accepting query params: `name`, `names`, `window`, `aggregation`, `agent_id`, `session_id`; delegates to MetricsStore.query_metrics
- [x] 4.3 Register monitoring router and WebSocket route in `src/hecate/main.py`; wire MonitoringService lifecycle (start/stop) into the application lifespan context manager

## 5. Integration — Wire TracingService to MetricsStore

- [x] 5.1 Update `TracingService.end_span()` in `src/hecate/services/observability/tracing.py` to accept an optional `metrics_store: MetricsStore | None = None` parameter; when provided, call `record_counter("requests_total")`, `record_histogram("request_latency_ms")`, `record_counter("errors_total")` on error, and `record_counter("input_tokens"/"output_tokens")` when usage data is present
- [x] 5.2 Wire the MetricsStore instance from MonitoringService into TracingService during application lifespan initialization

## 6. Tests

- [x] 6.1 Test `MetricsStore` ABC is not instantiable; `InMemoryMetricsStore` implements all abstract methods
- [x] 6.2 Test `InMemoryMetricsStore` accumulates counters, gauges, and histograms across time windows
- [x] 6.3 Test `InMemoryMetricsStore` garbage-collects expired entries on snapshot and enforces MAX_METRICS_BUFFER_SIZE cap
- [x] 6.4 Test `InMemoryMetricsStore.get_snapshot()` returns correct aggregations (sum, count, p50/p95/p99) for each window
- [x] 6.5 Test `ConnectionManager` handles connect, disconnect, broadcast, and stale connection cleanup
- [x] 6.6 Test `TimescaleMetricsStore` persists and queries metrics using test session_factory (SQLite fallback with date_trunc)
- [x] 6.7 Test WebSocket endpoint accepts connections, responds to ping, and receives at least one metric snapshot
- [x] 6.8 Test REST endpoint `GET /api/monitoring/metrics` returns correct metric values with query filters
- [x] 6.9 Test `TracingService.end_span()` updates MetricsStore counters and histograms when configured; completes normally when MetricsStore is None
- [x] 6.10 Test `get_metrics_store()` factory returns InMemoryMetricsStore by default and raises ValueError for unsupported types

## 7. Verification

- [x] 7.1 Run `ruff check src/hecate/ tests/` — 0 errors
- [x] 7.2 Run `ruff format --check src/ tests/` — 0 errors
- [x] 7.3 Run `mypy src/` — 0 errors
- [x] 7.4 Run `python -m pytest tests/ -q` — all tests pass (1564 passed)
