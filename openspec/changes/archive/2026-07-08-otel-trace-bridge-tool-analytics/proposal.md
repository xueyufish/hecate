## Why

Hecate's TraceModel table exists but receives zero data from production code. Workers (LLMWorker, ToolWorker) call `EnginePort.create_span()` which creates OpenTelemetry spans via the OTel SDK, but these spans only go through `ConsoleSpanExporter` (or are silently dropped). The dedicated `TracingService` (which writes to TraceModel) is fully implemented but never instantiated by any business code path. As a result, `signal_provider.ToolFailureRateProvider` queries `TraceModel.type == "tool"` and gets empty results, and the existing traces API (`GET /traces`) returns no data. Without bridging OTel spans to TraceModel, the entire observability layer is non-functional.

This change is the foundational infrastructure for Sprint 6 Ops Center (features 8.9a/b/c). It bridges OTel spans to TraceModel so tool execution analytics (8.9c) has real data to aggregate. Agent Health (8.9a) and Conversation Analytics (8.9b) in subsequent changes also benefit from the populated trace data.

## What Changes

- **New: `HecateTraceSpanProcessor`** — An OpenTelemetry `SpanProcessor` that intercepts span lifecycle events (`on_start`, `on_end`) and persists them to the `TraceModel` table. Uses an async queue + background consumer pattern (same as existing `AuditBatchWriter`) to bridge the sync OTel SDK with async SQLAlchemy.
- **New: PregelRuntime root trace span** — `PregelRuntime.execute()` creates a root OTel span at the start of each session execution, enabling full trace tree hierarchy. Child spans from Workers (LLM calls, tool calls) automatically nest under the root via OTel context propagation (contextvars in asyncio).
- **New: `ToolAnalyticsService`** — Aggregation service that queries TraceModel for per-tool metrics: success rate, P95 latency, top errors, execution trends, and per-agent/per-session drill-down.
- **New: REST API** — `GET /api/ops-center/tools/*` endpoints for tool analytics overview, per-tool details, trends, and top errors.
- **New: Frontend page** — Tool analytics dashboard at `web/src/app/(dashboard)/ops-center/tools/` with success-rate bar charts, latency tables, and error lists.
- **New: Sidebar entry** — "Ops Center" top-level navigation item.
- **Adopt: OpenTelemetry GenAI semantic conventions** — Tool spans use `gen_ai.tool.name`, `gen_ai.tool.type` attributes in addition to existing custom attributes, aligning with industry standards (Bedrock AgentCore, AgentScope, Dify all use OTel-native instrumentation).

## Capabilities

### New Capabilities

- `otel-trace-bridge`: OpenTelemetry SpanProcessor that bridges OTel spans to TraceModel. Includes span-to-TraceModel field mapping (type inference from span name prefix, attribute-to-metadata mapping), async write queue with background consumer, and registration in application startup. PregelRuntime root span creation for full trace tree hierarchy.
- `tool-execution-analytics`: Per-tool execution analytics derived from TraceModel data. Aggregation queries for success rate, P95 latency, error patterns, trends, and drill-down by agent/session. REST API and frontend dashboard.

### Modified Capabilities

_(none — this change introduces new capabilities without modifying existing spec requirements)_

## Impact

- **Engine layer**: `PregelRuntime.execute()` gains a root span wrapper (~10 lines, try/except guarded). No changes to Worker or WorkerPool interfaces.
- **Services layer**: New `ToolAnalyticsService` in `services/`. New `HecateTraceSpanProcessor` in `services/observability/`.
- **API layer**: New router at `api/management/tool_analytics.py`. Registered in `main.py`.
- **Config**: New settings (`TRACE_DB_EXPORT_ENABLED`, `TRACE_DB_QUEUE_MAX_SIZE`, `TRACE_DB_FLUSH_INTERVAL`).
- **Frontend**: New `ops-center/tools/` page + sidebar entry.
- **Dependencies**: No new packages — uses existing `opentelemetry-sdk` and `opentelemetry-api` already in pyproject.toml.
- **Tests**: New test files for SpanProcessor, ToolAnalyticsService, and API endpoints.
