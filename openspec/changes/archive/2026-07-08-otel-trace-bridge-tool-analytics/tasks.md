## 1. OTel Trace Bridge — SpanProcessor

- [x] 1.1 Add config settings: `TRACE_DB_EXPORT_ENABLED: bool = True`, `TRACE_DB_QUEUE_MAX_SIZE: int = 10000`, `TRACE_DB_FLUSH_INTERVAL: int = 5` in `core/config.py`
- [x] 1.2 Create `src/hecate/services/observability/span_processor.py` with `HecateTraceSpanProcessor` class implementing `SpanProcessor` interface (`on_start`, `on_end`, `shutdown`, `force_flush`)
- [x] 1.3 Implement span type inference: `"tool:"` → `"tool"`, `"llm:"`/`"llm_stream:"` → `"generation"`, `"session:"` → `"trace"`, else → `"span"`
- [x] 1.4 Implement async queue + background consumer: `asyncio.Queue` in `__init__`, `_consumer_loop()` background task that batches TraceModel inserts, graceful shutdown via `force_flush()`
- [x] 1.5 Implement attribute mapping: OTel attributes → `TraceModel.metadata_` (including `otel.trace_id` and `otel.span_id`), output attributes → `TraceModel.output_data`, usage attributes → `TraceModel.usage`
- [x] 1.6 Implement parent_id extraction from OTel parent span context (`span.parent.span_id` if present)

## 2. OTel Trace Bridge — Registration & Root Span

- [x] 2.1 Register `HecateTraceSpanProcessor` in `main.py` startup alongside existing `ConsoleSpanExporter` processor when `TRACE_DB_EXPORT_ENABLED=True`
- [x] 2.2 Start background consumer task in app startup, stop + flush in app shutdown
- [x] 2.3 Add root span creation in `PregelRuntime.execute()`: wrap execution body in `tracer.start_as_current_span("session:{session_id}")` with try/except guard
- [x] 2.4 Set root span attributes: `session.id`, `agent.id` (from execution context if available)
- [x] 2.5 Add `gen_ai.tool.name` attribute to ToolWorker's `create_span()` call alongside existing `tool_name`
- [x] 2.6 Add `gen_ai.request.model` attribute to LLMWorker's `create_span()` calls alongside existing `model`

## 3. OTel Trace Bridge — Tests

- [x] 3.1 Test `HecateTraceSpanProcessor.on_start()` creates TraceModel with correct type inference from name prefix
- [x] 3.2 Test `on_end()` updates status (completed/error), end_time, output_data, usage
- [x] 3.3 Test async queue: enqueue spans, run consumer, verify DB records
- [x] 3.4 Test queue full scenario: spans dropped with warning, no exception
- [x] 3.5 Test `force_flush()` drains queue on shutdown
- [x] 3.6 Test OTel trace_id/span_id stored in metadata_
- [x] 3.7 Test PregelRuntime root span creation and child span nesting (tool span parent = root span)
- [x] 3.8 Test PregelRuntime executes normally when tracing is disabled or fails

## 4. Tool Analytics Service

- [x] 4.1 Create `src/hecate/services/ops_center/tool_analytics.py` with `ToolAnalyticsService` class
- [x] 4.2 Implement `get_overview(start_date, end_date, agent_id=None)` → aggregate metrics (total, success_rate, avg_latency, p95_latency, unique_tools, error_count)
- [x] 4.3 Implement `get_tool_details(tool_name, start_date, end_date)` → per-tool metrics + top 5 errors
- [x] 4.4 Implement `get_trends(granularity, days, tool_name=None)` → time-series data points
- [x] 4.5 Implement `get_top_errors(limit, tool_name=None, start_date, end_date)` → sorted error entries

## 5. Tool Analytics API

- [x] 5.1 Create `src/hecate/api/management/tool_analytics.py` router with prefix `/api/ops-center/tools`
- [x] 5.2 Implement `GET /overview` endpoint (start_date, end_date, agent_id query params)
- [x] 5.3 Implement `GET /{tool_name}` endpoint (per-tool details, 404 if not found)
- [x] 5.4 Implement `GET /trends` endpoint (granularity, days, tool_name params)
- [x] 5.5 Implement `GET /errors` endpoint (limit, tool_name, date range params)
- [x] 5.6 Register router in `main.py`

## 6. Tool Analytics Tests

- [x] 6.1 Test `get_overview()` with populated TraceModel data (verify success_rate, p95, unique_tools)
- [x] 6.2 Test `get_overview()` with empty data (returns zeros, success_rate=1.0)
- [x] 6.3 Test `get_overview()` with agent_id filter
- [x] 6.4 Test `get_tool_details()` returns per-tool metrics and top errors
- [x] 6.5 Test `get_tool_details()` returns 404 for unknown tool
- [x] 6.6 Test `get_trends()` returns correct number of data points for daily/hourly granularity
- [x] 6.7 Test `get_top_errors()` sorting and limit

## 7. Frontend — Tool Analytics Dashboard

- [x] 7.1 Create `web/src/app/(dashboard)/ops-center/tools/page.tsx` with overview cards (total executions, success rate, P95 latency, error count)
- [x] 7.2 Add per-tool success rate bar chart (reuse `BarChart` component from `components/ui/bar-chart.tsx`)
- [x] 7.3 Add tool detail table (tool name, executions, success rate, avg latency, last used — sortable)
- [x] 7.4 Add top errors list with tool name, error message, count, timestamp
- [x] 7.5 Add time range selector (7d / 30d / custom) and empty state handling
- [x] 7.6 Add "Ops Center" entry to `web/src/components/sidebar.tsx` linking to `/ops-center/tools`

## 8. Verification

- [x] 8.1 Run `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 errors
- [x] 8.2 Run `mypy src/` — 0 errors
- [x] 8.3 Run `python -m pytest tests/test_observability/ tests/test_ops_center/ -q` — all pass
- [x] 8.4 Run frontend tests: `cd web && npx vitest run` — all pass
- [x] 8.5 Verify end-to-end: trigger a tool execution, confirm TraceModel record appears with correct type/status/parent
