## Purpose
Define the full-chain tracing capability for Hecate — trace → span → generation hierarchical observability with OpenTelemetry-compatible context propagation, async SQLAlchemy persistence, and REST API query support.

## Requirements

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

#### Scenario: Engine tracers route to the assembled provider
- **WHEN** the tracing pipeline is assembled at startup
- **THEN** the global tracer provider SHALL be set so spans created via `opentelemetry.trace.get_tracer()` (engine runtime, workers) reach the configured processors and exporters

### Requirement: MetricsStore wiring in the OTel span processor
The `HecateTraceSpanProcessor` SHALL feed the application MetricsStore from every completed span: per-type counters (`span.{type}.count`), duration histograms (`span.{type}.duration_ms`), error counters (`span.error.count`), and token totals (`tokens.input` / `tokens.output`). Metric recording SHALL require no additional instrumentation in the engine or worker layers.

#### Scenario: Successful span updates type counter and duration histogram
- **WHEN** a span named `tool:get_weather` ends with status OK
- **THEN** `MetricsStore.record_counter("span.tool.count", 1)` and `MetricsStore.record_histogram("span.tool.duration_ms", duration_ms)` SHALL be recorded

#### Scenario: Error span updates error counter
- **WHEN** a span ends with OTel status ERROR
- **THEN** `MetricsStore.record_counter("span.error.count", 1, tags={"type": <type>})` SHALL be recorded in addition

#### Scenario: Span with usage attributes updates token counters
- **WHEN** a completed span carries `usage.input_tokens` / `usage.output_tokens` attributes
- **THEN** `MetricsStore.record_counter("tokens.input", ...)` and `MetricsStore.record_counter("tokens.output", ...)` SHALL be recorded

#### Scenario: MetricsStore is optional (graceful degradation)
- **WHEN** no MetricsStore is configured (None)
- **THEN** span processing SHALL complete normally without recording metrics

### Requirement: Async persistence and external export via the OTel span processor
The `HecateTraceSpanProcessor` SHALL persist spans to the `traces` table via a bounded async queue with a background consumer (never blocking the synchronous OTel callback). External export SHALL use the standard OTLP HTTP/protobuf exporter, configured by `OTEL_EXPORTER_OTLP_ENDPOINT` / `OTEL_EXPORTER_OTLP_HEADERS` — switching backends (Langfuse, Tempo, Jaeger, Datadog OTLP ingest) is configuration-only. When no endpoint is set, spans SHALL go to the console exporter.

#### Scenario: Span written to the traces table asynchronously
- **WHEN** a span starts or ends
- **THEN** the record SHALL be enqueued and written by the background consumer without blocking the caller

#### Scenario: Queue overflow drops spans instead of blocking
- **WHEN** the persistence queue is full
- **THEN** the span record SHALL be dropped with a warning log and the original request SHALL complete normally

#### Scenario: External backend receives spans via OTLP
- **WHEN** `OTEL_EXPORTER_OTLP_ENDPOINT` points at an OTLP/HTTP receiver (e.g., Langfuse)
- **THEN** all spans SHALL be exported there with no code change

#### Scenario: No endpoint configured falls back to console
- **WHEN** `OTEL_EXPORTER_OTLP_ENDPOINT` is empty
- **THEN** spans SHALL be written to the console exporter (dev default)

#### Scenario: Flush pending spans on shutdown
- **WHEN** the application shuts down
- **THEN** pending span records in the async queue SHALL be flushed

### Requirement: Trace query REST API
The system SHALL expose REST API endpoints for querying trace data. Trace queries SHALL be tenant-scoped: results SHALL only include traces belonging to the caller's tenant scope (organization/workspace), and traces outside the caller's scope SHALL never be returned or enumerated.

#### Scenario: List traces with filters
- **WHEN** `GET /api/traces?session_id=<uuid>&agent_id=<uuid>&limit=20` is called
- **THEN** a paginated list of root trace records SHALL be returned, ordered by `start_time` descending, with fields: `trace_id`, `name`, `status`, `start_time`, `end_time`, `session_id`, `agent_id`, `usage` summary

#### Scenario: Get trace detail with span tree
- **WHEN** `GET /api/traces/{trace_id}` is called
- **THEN** the trace root record SHALL be returned with all child spans in a hierarchical tree structure, including `input_data`, `output_data`, `metadata`, `usage` for each span

#### Scenario: Traces filtered by time range
- **WHEN** `GET /api/traces?start_time=2026-01-01T00:00:00Z&end_time=2026-01-02T00:00:00Z` is called
- **THEN** only traces with `start_time` within the range SHALL be returned

#### Scenario: Tenant scoping on list
- **WHEN** traces exist for multiple tenants and a caller lists traces
- **THEN** only traces within the caller's tenant scope SHALL be returned

#### Scenario: Cross-tenant detail access
- **WHEN** `GET /api/traces/{trace_id}` is called for a trace outside the caller's tenant scope
- **THEN** the system SHALL return 404

### Requirement: Span creation in PregelRuntime and Workers
PregelRuntime and Workers SHALL create spans at execution boundaries via `RuntimePort.create_span` and `RuntimePort.end_span`. LLMWorker SHALL additionally instrument time-to-first-token (TTFT) for streaming LLM calls by recording `ttft_ms` in the span's metadata.

#### Scenario: PregelRuntime creates node execution span
- **WHEN** PregelRuntime starts executing a node
- **THEN** it SHALL call `runtime_port.create_span(name="node:{node_id}", attributes={"superstep": N})` and pass the returned `span_id` to the corresponding `_emit` call

#### Scenario: LLMWorker creates generation span
- **WHEN** LLMWorker calls `llm_invoke`
- **THEN** it SHALL create a span with `type="generation"`, `name="llm:{model}"`, and record `usage` (input_tokens, output_tokens) on span end

#### Scenario: LLMWorker records TTFT for streaming responses
- **WHEN** LLMWorker processes a streaming LLM response and receives the first chunk
- **THEN** it SHALL record `ttft_ms` (milliseconds from request start to first chunk arrival) in the span's `metadata` field

#### Scenario: ToolWorker creates tool span
- **WHEN** ToolWorker executes a tool
- **THEN** it SHALL create a span with `type="tool"`, `name="tool:{tool_name}"`, and record `output_data` on span end

#### Scenario: Security hook creates guardrail span
- **WHEN** a security hook scans input/output
- **THEN** it SHALL create a span with `type="span"`, `name="guardrail:{hook_name}"`, and record scan result in metadata
