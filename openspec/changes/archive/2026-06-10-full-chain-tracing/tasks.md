## 1. Dependencies and Data Model

- [x] 1.1 Add `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-instrumentation-fastapi` to `pyproject.toml` under a new `[observability]` optional dependency group; also add to `[dev]`
- [x] 1.2 Install the new dependencies with `uv pip install -e ".[dev]"`
- [x] 1.3 Create `TraceModel` ORM model in `src/hecate/models/trace.py` with fields: `id`, `trace_id`, `parent_id`, `type`, `name`, `session_id`, `agent_id`, `user_id`, `input_data`, `output_data`, `metadata`, `usage`, `level`, `status`, `start_time`, `end_time`, `created_at`; with indexes on `trace_id`, `session_id`, `agent_id`, `parent_id`
- [x] 1.4 Create Pydantic schemas `TraceCreateSchema`, `TraceReadSchema`, `TraceListSchema` in `src/hecate/models/trace.py`
- [x] 1.5 Add `TraceModel` to `src/hecate/models/__init__.py` exports
- [ ] 1.6 Create Alembic migration for the `traces` table

## 2. Engine Layer Changes

- [x] 2.1 Add `SpanContext` dataclass to `src/hecate/engine/ports.py` with fields: `span_id` (str), `trace_id` (str), `parent_id` (str | None)
- [x] 2.2 Add `create_span(name, parent_id=None, attributes=None) -> SpanContext | None` abstract method to `EnginePort` ABC
- [x] 2.3 Add `end_span(span_id, output_data=None, usage=None) -> None` abstract method to `EnginePort` ABC
- [x] 2.4 Add `trace_id: str | None = None` field to `Event` dataclass in `src/hecate/engine/eventstore.py`
- [x] 2.5 Update `InMemoryEventStore.append()` to preserve `trace_id` field in the versioned Event copy

## 3. Service Layer — TracingService Rewrite

- [x] 3.1 Rewrite `src/hecate/services/observability/tracing.py`: replace in-memory stub with production `TracingService` backed by async SQLAlchemy session; implement `start_trace`, `start_span`, `end_span`, `get_trace`, `list_traces` methods that write to `TraceModel`
- [x] 3.2 Create `src/hecate/services/observability/trace_manager.py` with `OpsTraceManager` singleton: async queue, `_worker_task` coroutine for dispatch, `on_trace_start`, `on_span_start`, `on_span_end`, `flush` methods
- [x] 3.3 Create `src/hecate/services/observability/trace_providers.py` with `TraceProvider` ABC and `NoOpTraceProvider` default implementation

## 4. EnginePort Adapter Implementations

- [x] 4.1 Update `_ProductionEnginePort` in `src/hecate/services/orchestration/engine_port_adapter.py` to implement `create_span` and `end_span` using OTel tracer and `TracingService`
- [x] 4.2 Update `AgentExecutionPort` in `src/hecate/services/orchestration/agent_execution_port.py` to implement `create_span` and `end_span`
- [x] 4.3 Extract `trace_id` from OTel context in both adapters and pass it through to `TracingService` calls

## 5. PregelRuntime and Worker Integration

- [x] 5.1 Update `PregelRuntime._emit()` in `src/hecate/engine/pregel.py` to accept `trace_id: str | None = None` parameter and pass it to `Event` construction
- [x] 5.2 Update all 8 `_emit()` call sites in `PregelRuntime.execute()` to pass `trace_id` from `EnginePort.create_span()` return value
- [x] 5.3 Update `LLMWorker` in `src/hecate/engine/workers/llm_worker.py` to create a generation span via `engine_port.create_span` before LLM calls and `end_span` after, passing `trace_id` to `_emit`
- [x] 5.4 Update `ToolWorker` in `src/hecate/engine/workers/tool_worker.py` to create a tool span via `engine_port.create_span` before tool calls and `end_span` after, passing `trace_id` to `_emit`

## 6. FastAPI Middleware

- [x] 6.1 Add OTel `FastAPIInstrumentor` setup in `src/hecate/main.py` lifespan: configure `TracerProvider`, `BatchSpanProcessor`, and instrument the app
- [x] 6.2 Add `TRACING_ENABLED` config flag to `src/hecate/core/config.py` (default `true`); skip OTel setup when `false`
- [x] 6.3 Add middleware or dependency to extract `agent_id`, `session_id`, `user_id` from request state and set as OTel span attributes

## 7. REST API

- [x] 7.1 Create `src/hecate/api/management/traces.py` router with `GET /api/traces` (list with query params: `session_id`, `agent_id`, `limit`, `offset`, `start_time`, `end_time`)
- [x] 7.2 Add `GET /api/traces/{trace_id}` endpoint returning trace detail with hierarchical span tree (using recursive CTE or in-memory tree construction)
- [x] 7.3 Register traces router in `src/hecate/main.py`

## 8. Tests

- [x] 8.1 Test `SpanContext` dataclass creation and field access
- [x] 8.2 Test `EnginePort.create_span` and `end_span` are abstract (cannot instantiate EnginePort without implementing them)
- [x] 8.3 Test `Event` dataclass accepts `trace_id` parameter and defaults to `None`
- [x] 8.4 Test `InMemoryEventStore.append()` preserves `trace_id` in stored events
- [x] 8.5 Test `TracingService.start_trace` creates a TraceModel with `status="started"`
- [x] 8.6 Test `TracingService.start_span` creates a child record with correct `parent_id` and `trace_id`
- [x] 8.7 Test `TracingService.end_span` updates status, output_data, usage, and end_time
- [x] 8.8 Test `TracingService.list_traces` with filters (session_id, agent_id, time range, pagination)
- [x] 8.9 Test `TracingService.get_trace` returns all records for a trace
- [ ] 8.10 Test `OpsTraceManager` dispatches to local DB and calls provider plugins
- [ ] 8.11 Test `TraceProvider` ABC is not instantiable; `NoOpTraceProvider` implements all methods as no-ops
- [ ] 8.12 Test `_ProductionEnginePort.create_span` returns `SpanContext` with valid IDs
- [ ] 8.13 Test `_ProductionEnginePort.create_span` returns `None` when no trace context
- [x] 8.14 Test traces API endpoints via httpx AsyncClient: list returns 200 with trace records, detail returns 200 with span tree, not-found returns 404
- [ ] 8.15 Test `main.py` OTel setup is skipped when `TRACING_ENABLED=false`
- [ ] 8.16 Test Alembic migration creates `traces` table with correct schema and indexes

## 9. Verification

- [x] 9.1 Run `ruff check src/hecate/ tests/` — 0 errors
- [x] 9.2 Run `ruff format --check src/ tests/` — 0 errors
- [x] 9.3 Run `mypy src/` — 0 errors
- [x] 9.4 Run `python -m pytest tests/ -q` — all tests pass
