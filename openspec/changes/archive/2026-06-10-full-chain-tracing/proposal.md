## Why

Hecate has four independent observability subsystems (EventStore, TracingService stub, MetricsCollector stub, StructuredLogger stub) that share no correlation ID. There is no way to trace a single user request from API entry through Service → Engine → Worker and back. This makes debugging agent behavior, attributing costs, and monitoring latency impossible in production. Industry research confirms that 6 out of 8 major cloud platforms (Google, AWS, Microsoft, Salesforce, IBM, Alibaba Cloud) use OpenTelemetry as the standard for agent tracing.

## What Changes

- Add OpenTelemetry-based tracing infrastructure with `opentelemetry-api` and `opentelemetry-sdk` as new dependencies
- Add `create_span` / `end_span` abstract methods to `EnginePort` so the engine layer can create spans without importing OTel directly
- Add `trace_id: str | None = None` parameter to `EventStore.append()` and the `Event` dataclass, correlating engine events with application-level traces
- Upgrade the existing `TracingService` stub to a production implementation backed by a new `traces` ORM table (observation-centric model with self-referencing parent_id)
- Add `OpsTraceManager` — an async queue + provider plugin system that writes traces to the local database and optionally exports to external providers (LangFuse, OTel Collector, etc.)
- Add `FastAPIInstrumentor` middleware in `main.py` that auto-creates root spans for every HTTP request and propagates `trace_id` via OTel `contextvars`
- Wire PregelRuntime and Workers to create OTel spans via `EnginePort.create_span` at execution boundaries (NODE_START/END, LLM_REQUEST/RESPONSE, TOOL_CALL/RESULT)
- Add REST API endpoints: `GET /api/traces` (list) and `GET /api/traces/{trace_id}` (detail with span tree)
- Add Alembic migration for the new `traces` table

## Capabilities

### New Capabilities
- `full-chain-tracing`: End-to-end distributed tracing from API entry to engine execution, with OTel context propagation, trace persistence, and query API

### Modified Capabilities
- `engine-ports`: Adding `create_span` and `end_span` abstract methods to EnginePort ABC for engine-layer span creation without direct OTel dependency
- `eventstore`: Adding `trace_id` field to Event dataclass and `append()` method for correlating engine events with application-level traces
- `core-infrastructure`: Adding OpenTelemetry SDK dependencies and FastAPI middleware instrumentation in main.py

## Impact

- **Dependencies**: New packages `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-instrumentation-fastapi` added to `pyproject.toml` (likely under a new `[observability]` optional group)
- **Engine layer**: `ports.py` gains 2 new abstract methods; `eventstore.py` Event dataclass gains `trace_id` field
- **Services layer**: `observability/tracing.py` completely rewritten from stub to production; new `observability/trace_manager.py` and `observability/trace_providers.py` files
- **API layer**: New `api/management/traces.py` router; `main.py` gains OTel middleware setup
- **Database**: New `traces` table via Alembic migration
- **Tests**: New test files for TracingService, OpsTraceManager, traces API; updated engine tests for new EnginePort methods and EventStore trace_id parameter
- **Breaking changes**: `EnginePort` ABC gains new abstract methods — all implementations (`_ProductionEnginePort`, `AgentExecutionPort`) must be updated. `Event` dataclass gains a new field (has default, not strictly breaking but affects frozen dataclass construction)
