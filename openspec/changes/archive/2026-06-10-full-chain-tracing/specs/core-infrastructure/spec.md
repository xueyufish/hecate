## MODIFIED Requirements

### Requirement: FastAPI application entry point
The `main.py` module SHALL initialize the FastAPI application with CORS middleware, unified error handling, lifespan events, health check endpoint, route registration, and OpenTelemetry instrumentation for automatic request tracing.

#### Scenario: OTel instrumentation enabled on startup
- **WHEN** the FastAPI application starts
- **THEN** `FastAPIInstrumentor` SHALL be configured to auto-create OTel spans for every HTTP request, with `opentelemetry-api` and `opentelemetry-sdk` as the tracing backend

#### Scenario: OTel span contains business attributes
- **WHEN** a request is processed that has `agent_id` and `session_id` in request state
- **THEN** the root OTel span SHALL include `agent_id` and `session_id` as attributes

#### Scenario: Tracing disabled via configuration
- **WHEN** `TRACING_ENABLED=false` environment variable is set
- **THEN** OTel instrumentation SHALL NOT be configured and no spans SHALL be created

#### Scenario: Health check endpoint
- **WHEN** `GET /health` is called
- **THEN** it SHALL return `{"status": "ok"}`
