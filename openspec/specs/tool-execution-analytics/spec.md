## ADDED Requirements

### Requirement: Tool analytics overview endpoint
The system SHALL expose `GET /api/ops-center/tools/overview` that returns aggregate tool execution metrics for a time range: total executions, overall success rate, average latency, P95 latency, unique tools used, and error count. Supports `start_date`, `end_date`, and optional `agent_id` filter.

#### Scenario: Overview for last 24 hours
- **WHEN** a client requests `GET /api/ops-center/tools/overview?start_date=2026-07-07T00:00:00Z&end_date=2026-07-08T00:00:00Z`
- **THEN** the response contains `{total_executions, success_rate, avg_latency_ms, p95_latency_ms, unique_tools, error_count}` computed from TraceModel records where type="tool"

#### Scenario: Overview filtered by agent
- **WHEN** a client requests with `agent_id={uuid}`
- **THEN** only tool spans for that agent are included in the aggregation

#### Scenario: No data returns zeros
- **WHEN** no tool spans exist in the time range
- **THEN** the response returns `{total_executions: 0, success_rate: 1.0, avg_latency_ms: 0, p95_latency_ms: 0, unique_tools: 0, error_count: 0}`

### Requirement: Per-tool details endpoint
The system SHALL expose `GET /api/ops-center/tools/{tool_name}` that returns detailed metrics for a specific tool: execution count, success rate, average latency, P95 latency, last used timestamp, and top 5 error messages with counts.

#### Scenario: Details for a specific tool
- **WHEN** a client requests `GET /api/ops-center/tools/get_weather?start_date=...&end_date=...`
- **THEN** the response contains `{tool_name, executions, success_rate, avg_latency_ms, p95_latency_ms, last_used_at, top_errors: [{message, count}]}`

#### Scenario: Unknown tool returns 404
- **WHEN** a client requests a tool name that has no trace records
- **THEN** the response is 404 with `"detail": "Tool not found"`

### Requirement: Tool trends endpoint
The system SHALL expose `GET /api/ops-center/tools/trends` that returns time-series data for tool executions. Each data point contains date, total executions, error count, and average latency. Supports `granularity` (hourly, daily, weekly), `days` parameter (1-90), and optional `tool_name` filter.

#### Scenario: Daily trends for 7 days
- **WHEN** a client requests `GET /api/ops-center/tools/trends?granularity=daily&days=7`
- **THEN** the response contains 7 data points, one per day, each with `{date, total, errors, avg_latency_ms}`

#### Scenario: Trends filtered by tool
- **WHEN** a client requests with `tool_name=get_weather`
- **THEN** only executions of "get_weather" are included in each data point

### Requirement: Top errors endpoint
The system SHALL expose `GET /api/ops-center/tools/errors` that returns the most frequent tool execution errors. Each entry contains tool name, error message, occurrence count, and last occurrence timestamp. Supports `limit` (default 20, max 100) and optional `tool_name` filter.

#### Scenario: Top errors across all tools
- **WHEN** a client requests `GET /api/ops-center/tools/errors?limit=10&start_date=...&end_date=...`
- **THEN** the response contains up to 10 error entries sorted by occurrence count descending

#### Scenario: Errors for specific tool
- **WHEN** a client requests with `tool_name=get_weather`
- **THEN** only errors from "get_weather" executions are returned

### Requirement: ToolAnalyticsService aggregation logic
The `ToolAnalyticsService` SHALL query TraceModel records where `type="tool"` and compute aggregations using SQL. Success rate SHALL be computed as `COUNT(status="completed") / COUNT(*)`. P95 latency SHALL be computed using `percentile_cont(0.95)` within `EXTRACT(EPOCH FROM (end_time - start_time)) * 1000`. Top errors SHALL be extracted from `output_data->>'error'` on records with `status="error"`.

#### Scenario: Success rate calculation
- **WHEN** 100 tool spans exist, 95 with status="completed" and 5 with status="error"
- **THEN** success_rate is `0.95`

#### Scenario: P95 latency from span durations
- **WHEN** tool spans have durations [10ms, 20ms, 30ms, ..., 1000ms]
- **THEN** p95_latency_ms is the 95th percentile value of all durations

### Requirement: Frontend tool analytics dashboard
The system SHALL provide a tool analytics page at `/ops-center/tools` with: an overview card row (total executions, success rate, P95 latency, error count), a per-tool bar chart (success rate by tool name), a tool detail table (sortable by executions/latency/error rate), and a top errors list. The page SHALL reuse existing Recharts `BarChart` and `LineChart` components.

#### Scenario: Dashboard renders with data
- **WHEN** the user navigates to `/ops-center/tools` and tool spans exist
- **THEN** the overview cards show metrics, the bar chart shows per-tool success rates, and the errors list displays recent failures

#### Scenario: Dashboard shows empty state
- **WHEN** no tool spans exist in the selected time range
- **THEN** the page displays a "No data" message with guidance on how to generate tool execution data

### Requirement: Ops Center sidebar navigation entry
The sidebar SHALL include an "Ops Center" top-level navigation item with a link to `/ops-center/tools`. The icon SHALL use `lucide-react`'s `LayoutDashboard` or `Gauge` icon.

#### Scenario: Sidebar shows Ops Center entry
- **WHEN** the dashboard sidebar renders
- **THEN** "Ops Center" appears as a navigation item linking to `/ops-center/tools`
