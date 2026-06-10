## Purpose
Define the real-time monitoring capability for Hecate — WebSocket-based metric streaming, time-windowed aggregation with dual-mode storage (InMemory + TimescaleDB), and REST metric query API.

## Requirements

### Requirement: MetricsStore ABC defines the metric persistence interface
The engine SHALL define a `MetricsStore` ABC with abstract methods for recording and querying time-windowed metrics. Two implementations SHALL be provided: `InMemoryMetricsStore` (default, zero external deps) and `TimescaleMetricsStore` (optional, requires TimescaleDB extension).

#### Scenario: Record a counter metric
- **WHEN** `record_counter(name="requests_total", value=1, tags={"agent_id": "abc"})` is called
- **THEN** the counter SHALL be incremented by the given value for the specified time bucket

#### Scenario: Record a histogram metric for latency
- **WHEN** `record_histogram(name="request_latency_ms", value=150.3, tags={"endpoint": "/v1/chat"})` is called
- **THEN** the value SHALL be appended to the histogram for the current time bucket

#### Scenario: Record a gauge metric
- **WHEN** `record_gauge(name="active_sessions", value=42, tags={})` is called
- **THEN** the gauge SHALL be set to the given value, replacing any previous value

#### Scenario: Query metrics for a time window
- **WHEN** `query_metrics(name="requests_total", window="5m", aggregation="sum")` is called
- **THEN** it SHALL return the aggregated metric value for the last 5 minutes

#### Scenario: Query percentile latency
- **WHEN** `query_metrics(name="request_latency_ms", window="5m", aggregation="p95")` is called
- **THEN** it SHALL return the 95th percentile latency value for the last 5 minutes

#### Scenario: Get a full snapshot of all metrics
- **WHEN** `get_snapshot(windows=["1m", "5m", "15m"])` is called
- **THEN** it SHALL return a `MetricsSnapshot` containing all counters, gauges, and histogram aggregations for each requested window

### Requirement: InMemoryMetricsStore provides zero-dependency default
The `InMemoryMetricsStore` SHALL use ring buffers organized by time window (1m, 5m, 15m, 1h). Each metric event appends to all applicable windows. Expired data SHALL be garbage-collected on each snapshot query.

#### Scenario: Metrics accumulate in time windows
- **WHEN** 100 counter increments are recorded over 2 minutes
- **THEN** the 1m window SHALL contain only events from the last 60 seconds, while the 5m window SHALL contain all 100 events

#### Scenario: Expired data is garbage-collected
- **WHEN** a snapshot is requested and a 1m ring buffer has entries older than 60 seconds
- **THEN** those entries SHALL be removed during the snapshot computation

#### Scenario: Memory cap enforcement
- **WHEN** the number of entries in any single ring buffer exceeds `MAX_METRICS_BUFFER_SIZE` (default 100,000)
- **THEN** the oldest entries SHALL be evicted to stay within the cap

### Requirement: TimescaleMetricsStore uses PostgreSQL time_bucket for aggregation
The `TimescaleMetricsStore` SHALL persist metrics to a `metrics` table (or hypertable) and use TimescaleDB's `time_bucket()` function for server-side aggregation. When TimescaleDB is not installed, it SHALL fall back to standard PostgreSQL date_trunc aggregation.

#### Scenario: Persist a metric to the database
- **WHEN** `record_counter(name="requests_total", value=1, tags={"agent_id": "abc"})` is called
- **THEN** a row SHALL be inserted into the metrics table with timestamp, name, value, and tags

#### Scenario: Query with time_bucket aggregation
- **WHEN** `query_metrics(name="requests_total", window="5m", aggregation="sum")` is called and TimescaleDB is available
- **THEN** it SHALL use `time_bucket('5 minutes', timestamp)` for grouping

#### Scenario: Fallback without TimescaleDB
- **WHEN** TimescaleDB extension is not installed
- **THEN** the store SHALL use `date_trunc('minute', timestamp)` as a fallback with equivalent results

### Requirement: WebSocket endpoint pushes metric snapshots
The system SHALL expose a WebSocket endpoint at `/ws/monitoring` that accepts connections from dashboard clients and pushes a `MetricsSnapshot` JSON every 5 seconds (configurable via `METRICS_PUSH_INTERVAL`).

#### Scenario: Client connects and receives snapshots
- **WHEN** a WebSocket client connects to `/ws/monitoring`
- **THEN** it SHALL receive a JSON snapshot every `METRICS_PUSH_INTERVAL` seconds containing all metric windows

#### Scenario: Client subscribes to specific metrics
- **WHEN** a connected client sends `{"action": "subscribe", "metrics": ["error_rate", "p95_latency"]}`
- **THEN** subsequent snapshots SHALL include only the requested metrics

#### Scenario: Client sends ping
- **WHEN** a connected client sends `{"action": "ping"}`
- **THEN** the server SHALL respond with `{"type": "pong"}`

#### Scenario: Server shutdown notification
- **WHEN** the application is shutting down
- **THEN** the server SHALL send `{"type": "shutdown"}` to all connected clients before closing connections

#### Scenario: Stale connection cleanup
- **WHEN** a WebSocket client disconnects without close frame
- **THEN** the connection SHALL be removed from the active set on the next failed send attempt

### Requirement: REST API for on-demand metric queries
The system SHALL expose `GET /api/monitoring/metrics` endpoint accepting query parameters for metric name, time window, aggregation function, and tag filters.

#### Scenario: Query a specific metric
- **WHEN** `GET /api/monitoring/metrics?name=requests_total&window=5m&aggregation=sum` is called
- **THEN** it SHALL return the aggregated metric value for the last 5 minutes

#### Scenario: Query with tag filters
- **WHEN** `GET /api/monitoring/metrics?name=requests_total&window=15m&aggregation=sum&agent_id=abc` is called
- **THEN** it SHALL return the sum filtered to the specified agent_id tag

#### Scenario: Query multiple metrics at once
- **WHEN** `GET /api/monitoring/metrics?names=requests_total,error_count&window=5m` is called
- **THEN** it SHALL return both metrics in a single response

#### Scenario: List available metrics
- **WHEN** `GET /api/monitoring/metrics` is called without `name` or `names` parameter
- **THEN** it SHALL return a list of all available metric names with their current values across all windows

### Requirement: MonitoringService orchestrates metric collection and push
A `MonitoringService` SHALL manage the MetricsStore, the WebSocket ConnectionManager, and the background push task. It SHALL be initialized during application lifespan startup and stopped gracefully on shutdown.

#### Scenario: Service starts with application
- **WHEN** the FastAPI application starts
- **THEN** `MonitoringService` SHALL initialize the configured MetricsStore and start the background push task

#### Scenario: Service stops gracefully
- **WHEN** the FastAPI application shuts down
- **THEN** the push task SHALL be cancelled, a shutdown message SHALL be sent to all WebSocket clients, and all connections SHALL be closed

#### Scenario: Push task broadcasts snapshots
- **WHEN** the push task timer fires (every `METRICS_PUSH_INTERVAL` seconds)
- **THEN** it SHALL call `MetricsStore.get_snapshot()`, serialize to JSON, and broadcast to all connected WebSocket clients
