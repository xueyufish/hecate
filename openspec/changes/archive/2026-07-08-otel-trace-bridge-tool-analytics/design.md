## Context

Hecate has a complete trace infrastructure that is disconnected:

- **TraceModel** (`models/trace.py`): Full ORM model with trace_id, parent_id, type (trace/span/generation/tool/retrieval), session_id, agent_id, usage, status, start/end_time. Schema is LangFuse v4-inspired observation-centric single-table design.
- **TracingService** (`services/observability/tracing.py`): Complete implementation with start_trace/start_span/end_span + MetricsStore integration. But **zero call sites** in business code.
- **EnginePortAdapter.create_span()** (`services/orchestration/engine_port_adapter.py`): Workers call this, which uses `opentelemetry.trace.get_tracer().start_span()`. Spans go through OTel SDK pipeline but only reach `ConsoleSpanExporter`. **Does not write to TraceModel.**
- **signal_provider.ToolFailureRateProvider**: Queries `TraceModel.type == "tool"` — gets empty results because nothing writes tool spans to TraceModel.
- **OTel config** (`main.py`): `TracerProvider` with `BatchSpanProcessor(ConsoleSpanExporter())`. FastAPI auto-instrumentation enabled.

Workers (LLMWorker, ToolWorker) already call `self._port.create_span(name, attributes)` at execution boundaries. The span names follow conventions: `"llm:{node_id}"`, `"llm_stream:{node_id}"`, `"tool:{tool_name}"`.

PregelRuntime.execute() accepts a `trace_id` parameter and passes it to workers via `execution_context`, but callers (WorkflowExecutionService) don't supply it.

## Goals / Non-Goals

**Goals:**

- Bridge OTel spans to TraceModel so trace data is persisted in the database
- Enable full trace tree hierarchy (root session span → child LLM/tool spans)
- Make `signal_provider.ToolFailureRateProvider` functional with real data
- Provide tool execution analytics API and dashboard (feature 8.9c)
- Adopt OpenTelemetry GenAI semantic conventions for attribute naming
- Zero Worker code changes (OTel context auto-propagation)

**Non-Goals:**

- Agent Health Monitoring (8.9a) — Change 2
- Conversation Analytics (8.9b) — Change 3
- Unified Ops Center Dashboard (8.9) — Change 4
- Real-time WebSocket push for tool analytics (REST polling only)
- LLM-as-judge quality scoring of tool results
- OTLP exporter to external backends (Jaeger/Tempo) — future enhancement
- Modifying Worker or WorkerPool interfaces

## Decisions

### Decision 1: OTel SpanProcessor (not EnginePortAdapter modification)

**Choice**: Implement `HecateTraceSpanProcessor` (extends `opentelemetry.sdk.trace.SpanProcessor`) registered with the OTel TracerProvider.

**Rationale**: All 7 reference platforms (Bedrock AgentCore, AgentScope, IBM watsonx, Salesforce Agentforce, Huawei AgentArts, Dify, LangFuse) use OTel-native SpanProcessor/exporter patterns. This approach:
- Requires zero changes to Worker code or EnginePortAdapter
- Automatically captures all OTel spans (including FastAPI auto-instrumentation)
- Enables future OTLP export to external backends by adding another processor
- Follows the W3C OpenTelemetry standard

**Alternatives considered**:
- **Modify EnginePortAdapter** to write TraceModel directly: Would work, but only covers spans created via EnginePort. Misses FastAPI-instrumented spans. Deviates from industry standard. Every new adapter implementation needs the same modification.
- **Dual-write (OTel + TracingService)**: Maintenance burden of keeping two paths in sync.

### Decision 2: Async bridge via queue + background consumer

**Choice**: SpanProcessor enqueues span data to an `asyncio.Queue`; a background consumer task (started in app lifecycle) batches and writes to TraceModel via async SQLAlchemy session.

**Rationale**: OTel SpanProcessor methods (`on_start`, `on_end`) are synchronous. SQLAlchemy async sessions require `await`. The queue pattern bridges this gap and is already proven in Hecate:
- `AuditBatchWriter` (`services/audit/writer.py`): Same pattern — queue + background `_drain_loop()`
- `OpsTraceManager` (`services/observability/trace_manager.py`): Same pattern — queue + `_worker()`

**Alternatives considered**:
- `asyncio.run_coroutine_threadsafe()`: Per-span DB call, no batching, higher overhead
- Synchronous DB session in SpanProcessor: Blocks the calling thread (Worker's async event loop), unacceptable

### Decision 3: Full trace tree via PregelRuntime root span

**Choice**: `PregelRuntime.execute()` wraps its execution in `tracer.start_as_current_span("session:{session_id}")`. Child spans from Workers auto-nest via OTel contextvars propagation in asyncio.

**Rationale**:
- DirectWorkerPool uses `await worker.execute()` — same asyncio Task, contextvars propagate automatically
- ToolWorker uses sequential `for tc in tool_calls` — no `asyncio.gather` or `to_thread`, context propagates
- LLMWorker uses `async for token in llm_invoke()` — same Task, context propagates
- Full trace tree is the industry standard (Bedrock's TracePart hierarchy, Salesforce Session Trace Data Model, Huawei's 调用链分析)

**Alternatives considered**:
- **session_id + agent_id only (no tree)**: Would require modifying Workers to pass session_id in span attributes. More code changes than root span approach. No drill-down capability.
- **Manual trace_id propagation**: PregelRuntime already has `trace_id` parameter, but callers don't pass it. Root span is cleaner.

### Decision 4: Span type inference from name prefix

**Choice**: SpanProcessor infers TraceModel.type from the OTel span name:
- `"tool:"` prefix → type = `"tool"`
- `"llm:"` or `"llm_stream:"` prefix → type = `"generation"`
- `"session:"` prefix → type = `"trace"` (root)
- Everything else → type = `"span"`

**Rationale**: Matches existing conventions in LLMWorker (`f"llm:{node_id}"`) and ToolWorker (`f"tool:{name}"`). Aligns with TraceModel's SpanType enum. No Worker changes needed.

### Decision 5: REST polling for analytics API

**Choice**: All tool analytics endpoints are REST with query parameters (start_date, end_date, group_by). No WebSocket push.

**Rationale**: All 7 reference platforms use polling for analytics dashboards. WebSocket/SSE is only used for real-time alerting (already handled by existing AlertService). Tool analytics data is aggregate queries, not event streams — polling is the natural fit.

## Risks / Trade-offs

- **[Risk] PregelRuntime modification risk** → Root span wrapped in try/except; if OTel fails, execution continues normally. Existing PregelRuntime tests verify execution behavior is unchanged.
- **[Risk] SpanProcessor write latency** → Queue-based writes may lag behind by a few seconds. Acceptable for analytics (not real-time alerting). AlertService evaluates signals on its own schedule.
- **[Risk] DB write volume increase** → Every span = 1 DB row. For high-traffic deployments, consider sampling. For now, all spans are written (consistent with current ConsoleSpanExporter behavior). Config setting `TRACE_DB_QUEUE_MAX_SIZE` prevents unbounded memory growth.
- **[Trade-off] OTel trace_id (128-bit hex) vs TraceModel trace_id (UUID)** → Store OTel trace_id hex string in `metadata_["otel.trace_id"]` for cross-referencing. Generate a UUID for `TraceModel.trace_id` field. Root span's UUID is the trace_id; child spans share it via OTel context.
- **[Trade-off] No cross-process trace propagation yet** → DirectWorkerPool is single-process. Temporal WorkerPool (future) would need W3C trace context headers. Out of scope for this change.
