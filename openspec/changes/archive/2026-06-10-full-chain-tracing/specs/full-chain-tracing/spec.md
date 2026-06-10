## ADDED Requirements

### Requirement: Trace record persistence with observation-centric model
The system SHALL persist trace and span data in a `traces` table with self-referencing `parent_id` foreign key. Each record SHALL have fields: `id` (UUID PK), `trace_id` (UUID, shared within a trace), `parent_id` (UUID FK, nullable), `type` (varchar: trace, span, generation, tool, retrieval), `name`, `session_id`, `agent_id`, `user_id`, `input_data` (JSONB), `output_data` (JSONB), `metadata` (JSONB), `usage` (JSONB), `level` (varchar), `status` (varchar: started, completed, error), `start_time` (timestamptz), `end_time` (timestamptz), `created_at` (timestamptz).

#### Scenario: Create a root trace record
- **WHEN** a trace is started with `session_id`, `agent_id`, `name="chat_request"`
- **THEN** a record SHALL be created with `parent_id=NULL`, `type="trace"`, `status="started"`, and auto-generated `id`, `trace_id`, `start_time`, `created_at`

#### Scenario: Create a child span under a trace
- **WHEN** a span is started with `parent_id=<root_trace_id>`, `name="llm_call"`, `type="generation"`
- **THEN** a record SHALL be created with the specified `parent_id`, same `trace_id` as parent, and `status="started"`

#### Scenario: Complete a span with output and usage
- **WHEN** `end_span` is called with `span_id`, `output_data={"text": "response"}`, `usage={"input_tokens": 100, "output_tokens": 50}`
- **THEN** the record SHALL be updated with `status="completed"`, `end_time=now()`, and the provided output and usage data

### Requirement: OTel context propagation from API to engine
The system SHALL propagate `trace_id` from FastAPI middleware through Service → Engine → Worker layers using OpenTelemetry `contextvars`. Every HTTP request SHALL auto-create an OTel root span via `FastAPIInstrumentor`.

#### Scenario: Trace context created for HTTP request
- **WHEN** a POST request hits `/api/sessions`
- **THEN** an OTel root span SHALL be created automatically with `trace_id` accessible from the active span context

#### Scenario: Trace ID extracted at service boundary
- **WHEN** a service method needs the current `trace_id`
- **THEN** it SHALL extract it from the active OTel span context via `opentelemetry.trace.get_current_span().get_span_context().trace_id`

#### Scenario: No active trace context
- **WHEN** code runs outside an HTTP request (e.g., background task, CLI)
- **THEN** `trace_id` SHALL be `None`, and span creation SHALL be a no-op (not raise)

### Requirement: OpsTraceManager async queue with provider plugins
The system SHALL provide an `OpsTraceManager` singleton that dispatches trace events to both local database persistence and optional external providers via async queue.

#### Scenario: Trace written to local database
- **WHEN** `OpsTraceManager.on_span_end(span_data)` is called
- **THEN** the span data SHALL be persisted to the `traces` table immediately

#### Scenario: Trace dispatched to external provider
- **WHEN** a `LangFuseProvider` is configured and a span ends
- **THEN** the span data SHALL be queued for async dispatch to LangFuse without blocking the caller

#### Scenario: Provider failure does not affect request
- **WHEN** an external provider raises an exception during dispatch
- **THEN** the error SHALL be logged but the original request SHALL complete normally

#### Scenario: Flush pending traces on shutdown
- **WHEN** the application shuts down
- **THEN** all pending trace events in the async queue SHALL be flushed to configured providers

### Requirement: Trace query REST API
The system SHALL expose REST API endpoints for querying trace data.

#### Scenario: List traces with filters
- **WHEN** `GET /api/traces?session_id=<uuid>&agent_id=<uuid>&limit=20` is called
- **THEN** a paginated list of root trace records SHALL be returned, ordered by `start_time` descending, with fields: `trace_id`, `name`, `status`, `start_time`, `end_time`, `session_id`, `agent_id`, `usage` summary

#### Scenario: Get trace detail with span tree
- **WHEN** `GET /api/traces/{trace_id}` is called
- **THEN** the trace root record SHALL be returned with all child spans in a hierarchical tree structure, including `input_data`, `output_data`, `metadata`, `usage` for each span

#### Scenario: Traces filtered by time range
- **WHEN** `GET /api/traces?start_time=2026-01-01T00:00:00Z&end_time=2026-01-02T00:00:00Z` is called
- **THEN** only traces with `start_time` within the range SHALL be returned

### Requirement: Span creation in PregelRuntime and Workers
PregelRuntime and Workers SHALL create spans at execution boundaries via `EnginePort.create_span` and `EnginePort.end_span`.

#### Scenario: PregelRuntime creates node execution span
- **WHEN** PregelRuntime starts executing a node
- **THEN** it SHALL call `engine_port.create_span(name="node:{node_id}", attributes={"superstep": N})` and pass the returned `span_id` to the corresponding `_emit` call

#### Scenario: LLMWorker creates generation span
- **WHEN** LLMWorker calls `llm_invoke`
- **THEN** it SHALL create a span with `type="generation"`, `name="llm:{model}"`, and record `usage` (input_tokens, output_tokens) on span end

#### Scenario: ToolWorker creates tool span
- **WHEN** ToolWorker executes a tool
- **THEN** it SHALL create a span with `type="tool"`, `name="tool:{tool_name}"`, and record `output_data` on span end

#### Scenario: Security hook creates guardrail span
- **WHEN** a security hook scans input/output
- **THEN** it SHALL create a span with `type="span"`, `name="guardrail:{hook_name}"`, and record scan result in metadata
