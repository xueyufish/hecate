## ADDED Requirements

### Requirement: HecateTraceSpanProcessor bridges OTel spans to TraceModel
The system SHALL implement `HecateTraceSpanProcessor` that implements the OpenTelemetry `SpanProcessor` interface (`on_start`, `on_end`, `shutdown`, `force_flush`). On span start, the processor SHALL create a `TraceModel` record with `start_time`, `name`, `type` (inferred from span name prefix), and `metadata_` (from OTel attributes). On span end, the processor SHALL update the record with `end_time`, `status` (completed or error), `output_data` (from output attributes), and `usage` (from usage attributes).

#### Scenario: Tool span creates TraceModel with type="tool"
- **WHEN** ToolWorker creates a span named `"tool:get_weather"` with attributes `{"tool_name": "get_weather", "arguments": "{...}"}`
- **THEN** the processor creates a TraceModel record with `type="tool"`, `name="tool:get_weather"`, and `metadata_` containing the attributes

#### Scenario: LLM span creates TraceModel with type="generation"
- **WHEN** LLMWorker creates a span named `"llm:agent_node_1"` with attributes `{"model": "gpt-4o", "message_count": 5}`
- **THEN** the processor creates a TraceModel record with `type="generation"`, `name="llm:agent_node_1"`, and `metadata_` containing the model and message_count

#### Scenario: Root session span creates TraceModel with type="trace"
- **WHEN** PregelRuntime creates a root span named `"session:{session_id}"`
- **THEN** the processor creates a TraceModel record with `type="trace"`, `parent_id=None`, and `metadata_` containing `session_id` and `agent_id`

#### Scenario: Span end updates status and end_time
- **WHEN** a span ends normally with output attributes `{"output.result_length": "42"}`
- **THEN** the processor updates the TraceModel record with `status="completed"`, `end_time` set to current time, and `output_data={"result_length": "42"}`

#### Scenario: Errored span sets status to error
- **WHEN** a span ends with an exception or output attribute `{"output.error": "Connection refused"}`
- **THEN** the processor updates the TraceModel record with `status="error"` and records the error message in `output_data`

### Requirement: Span type inference from name prefix
The processor SHALL infer TraceModel.type from the OTel span name using prefix matching: `"tool:"` → `"tool"`, `"llm:"` or `"llm_stream:"` → `"generation"`, `"session:"` → `"trace"`. Spans with unrecognized prefixes SHALL default to `type="span"`.

#### Scenario: Unrecognized prefix defaults to span
- **WHEN** a span named `"custom_operation:data_sync"` is processed
- **THEN** the TraceModel record has `type="span"`

### Requirement: Async write queue with background consumer
The processor SHALL use an `asyncio.Queue` to buffer span data and a background consumer task to batch-write TraceModel records via async SQLAlchemy. The queue SHALL have a configurable maximum size (`TRACE_DB_QUEUE_MAX_SIZE`, default 10000). When the queue is full, new spans SHALL be silently dropped (with a warning log) to prevent unbounded memory growth.

#### Scenario: Background consumer processes queued spans
- **WHEN** 5 spans are enqueued and the background consumer flushes
- **THEN** 5 TraceModel records are persisted to the database in a batch

#### Scenario: Queue full drops spans with warning
- **WHEN** the queue is at maximum capacity and a new span is created
- **THEN** the span is not enqueued, a WARNING is logged, and application execution continues normally

#### Scenario: Application shutdown flushes queue
- **WHEN** the application receives a shutdown signal
- **THEN** the processor calls `force_flush()` which drains remaining spans from the queue before shutdown

### Requirement: OTel trace_id stored in metadata
The processor SHALL store the OTel trace ID (128-bit hex string) and span ID (64-bit hex string) in the TraceModel `metadata_` JSON field as `otel.trace_id` and `otel.span_id`. Child spans SHALL share the same trace ID from OTel context propagation.

#### Scenario: Trace ID stored for cross-referencing
- **WHEN** a span with OTel trace_id `"0af7651916cd43dd8448eb211c80319c"` is processed
- **THEN** the TraceModel record's `metadata_` contains `"otel.trace_id": "0af7651916cd43dd8448eb211c80319c"`

### Requirement: Processor registration in application startup
The system SHALL register `HecateTraceSpanProcessor` with the OTel `TracerProvider` during application startup when `TRACING_ENABLED` is `True` and `TRACE_DB_EXPORT_ENABLED` is `True`. The processor SHALL be added alongside the existing `ConsoleSpanExporter` processor (not replacing it).

#### Scenario: Processor registered when tracing enabled
- **WHEN** the application starts with `TRACING_ENABLED=True` and `TRACE_DB_EXPORT_ENABLED=True`
- **THEN** the TracerProvider has both `BatchSpanProcessor(ConsoleSpanExporter)` and `HecateTraceSpanProcessor` registered

#### Scenario: Processor not registered when DB export disabled
- **WHEN** `TRACE_DB_EXPORT_ENABLED=False` (default for tests)
- **THEN** the TracerProvider only has the console exporter, and no DB writes occur

### Requirement: PregelRuntime creates root trace span
The `PregelRuntime.execute()` method SHALL create a root OTel span named `"session:{session_id}"` at the start of execution, with attributes for `session.id` and `agent.id` (if available). The root span SHALL be created using `tracer.start_as_current_span()` so child spans from Workers automatically nest via contextvars. If span creation fails, execution SHALL continue normally without tracing.

#### Scenario: Root span created for session execution
- **WHEN** PregelRuntime.execute() is called with session_id=uuid
- **THEN** a root span "session:{session_id}" is created and set as the current OTel context for the duration of execution

#### Scenario: Child tool spans nest under root
- **WHEN** ToolWorker creates span "tool:get_weather" during a session execution
- **THEN** the tool span's parent is the session root span, and both share the same OTel trace_id

#### Scenario: Tracing failure does not break execution
- **WHEN** OTel tracer is unavailable or span creation raises an exception
- **THEN** PregelRuntime.execute() continues normally, logs a debug message, and produces correct results

### Requirement: OpenTelemetry GenAI semantic convention attributes
Tool spans SHALL include `gen_ai.tool.name` attribute (from tool name) alongside the existing custom `tool_name` attribute. LLM spans SHALL include `gen_ai.request.model` attribute (from model name) alongside the existing custom `model` attribute. These standard attributes enable interoperability with OTel-compatible backends.

#### Scenario: Tool span includes gen_ai.tool.name
- **WHEN** ToolWorker creates a span for tool "get_weather"
- **THEN** the span attributes include both `"tool_name": "get_weather"` and `"gen_ai.tool.name": "get_weather"`
