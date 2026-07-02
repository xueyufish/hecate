## MODIFIED Requirements

### Requirement: Settings loaded from environment variables and .env file
The `Settings` class (pydantic-settings) SHALL include monitoring-related settings: `METRICS_STORE_TYPE` (default `"in_memory"`, values: `"in_memory"` | `"timescale"`), `METRICS_PUSH_INTERVAL` (default `5`, seconds), and `MAX_METRICS_BUFFER_SIZE` (default `100000`, max entries per ring buffer).

#### Scenario: Default monitoring configuration
- **WHEN** no monitoring-related environment variables are set
- **THEN** `METRICS_STORE_TYPE="in_memory"`, `METRICS_PUSH_INTERVAL=5`, `MAX_METRICS_BUFFER_SIZE=100000`

#### Scenario: Enable TimescaleDB metrics store
- **WHEN** `METRICS_STORE_TYPE=timescale`
- **THEN** the `TimescaleMetricsStore` SHALL be used, connecting to the configured `DATABASE_URL`

### Requirement: FastAPI application entry point with OpenTelemetry instrumentation
The `main.py` module SHALL register the monitoring WebSocket route and REST endpoint, and start/stop the `MonitoringService` during application lifespan.

#### Scenario: Monitoring routes registered on startup
- **WHEN** the FastAPI application starts
- **THEN** the `/ws/monitoring` WebSocket route and `/api/monitoring/metrics` REST endpoint SHALL be accessible

#### Scenario: MonitoringService lifecycle in lifespan
- **WHEN** the application lifespan starts
- **THEN** `MonitoringService.start()` SHALL be called; on lifespan shutdown, `MonitoringService.stop()` SHALL be called
