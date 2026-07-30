## ADDED Requirements — 新增需求

### Requirement: Tool analytics overview endpoint — 需求：工具分析概览端点
系统应暴露 `GET /api/ops-center/tools/overview`，返回指定时间范围内的聚合工具执行指标：总执行次数、总体成功率、平均延迟、P95 延迟、使用的唯一工具数及错误计数。支持 `start_date`、`end_date` 和可选的 `agent_id` 过滤。

#### Scenario: Overview for last 24 hours — 场景：过去 24 小时的概览
- **WHEN** 客户端请求 `GET /api/ops-center/tools/overview?start_date=2026-07-07T00:00:00Z&end_date=2026-07-08T00:00:00Z`
- **THEN** 响应包含 `{total_executions, success_rate, avg_latency_ms, p95_latency_ms, unique_tools, error_count}`，根据 type="tool" 的 TraceModel 记录计算得出

#### Scenario: Overview filtered by agent — 场景：按 agent 过滤的概览
- **WHEN** 客户端使用 `agent_id={uuid}` 请求
- **THEN** 聚合中仅包含该 agent 的工具 spans

#### Scenario: No data returns zeros — 场景：无数据返回零值
- **WHEN** 时间范围内不存在工具 spans
- **THEN** 响应返回 `{total_executions: 0, success_rate: 1.0, avg_latency_ms: 0, p95_latency_ms: 0, unique_tools: 0, error_count: 0}`

### Requirement: Per-tool details endpoint — 需求：每个工具的详细信息端点
系统应暴露 `GET /api/ops-center/tools/{tool_name}`，返回特定工具的详细指标：执行次数、成功率、平均延迟、P95 延迟、最后使用时间戳以及前 5 条错误消息及其计数。

#### Scenario: Details for a specific tool — 场景：特定工具的详细信息
- **WHEN** 客户端请求 `GET /api/ops-center/tools/get_weather?start_date=...&end_date=...`
- **THEN** 响应包含 `{tool_name, executions, success_rate, avg_latency_ms, p95_latency_ms, last_used_at, top_errors: [{message, count}]}`

#### Scenario: Unknown tool returns 404 — 场景：未知工具返回 404
- **WHEN** 客户端请求一个没有追踪记录的工具名称
- **THEN** 响应为 404，带有 `"detail": "Tool not found"`

### Requirement: Tool trends endpoint — 需求：工具趋势端点
系统应暴露 `GET /api/ops-center/tools/trends`，返回工具执行的时间序列数据。每个数据点包含日期、总执行次数、错误计数和平均延迟。支持 `granularity`（hourly、daily、weekly）、`days` 参数（1-90）和可选的 `tool_name` 过滤。

#### Scenario: Daily trends for 7 days — 场景：7 天的每日趋势
- **WHEN** 客户端请求 `GET /api/ops-center/tools/trends?granularity=daily&days=7`
- **THEN** 响应包含 7 个数据点，每天一个，每个包含 `{date, total, errors, avg_latency_ms}`

#### Scenario: Trends filtered by tool — 场景：按工具过滤的趋势
- **WHEN** 客户端使用 `tool_name=get_weather` 请求
- **THEN** 每个数据点仅包含 "get_weather" 的执行

### Requirement: Top errors endpoint — 需求：顶级错误端点
系统应暴露 `GET /api/ops-center/tools/errors`，返回最频繁的工具执行错误。每个条目包含工具名称、错误消息、出现次数和最后出现时间戳。支持 `limit`（默认 20，最大 100）和可选的 `tool_name` 过滤。

#### Scenario: Top errors across all tools — 场景：所有工具的顶级错误
- **WHEN** 客户端请求 `GET /api/ops-center/tools/errors?limit=10&start_date=...&end_date=...`
- **THEN** 响应包含最多 10 个错误条目，按出现次数降序排列

#### Scenario: Errors for specific tool — 场景：特定工具的错误
- **WHEN** 客户端使用 `tool_name=get_weather` 请求
- **THEN** 仅返回来自 "get_weather" 执行的错误

### Requirement: ToolAnalyticsService aggregation logic — 需求：ToolAnalyticsService 聚合逻辑
`ToolAnalyticsService` 应查询 `type="tool"` 的 TraceModel 记录，并使用 SQL 计算聚合。成功率应计算为 `COUNT(status="completed") / COUNT(*)`。P95 延迟应使用 `percentile_cont(0.95)` 在 `EXTRACT(EPOCH FROM (end_time - start_time)) * 1000` 内计算。顶级错误应从 `status="error"` 记录的 `output_data->>'error'` 中提取。

#### Scenario: Success rate calculation — 场景：成功率计算
- **WHEN** 存在 100 个工具 spans，95 个 status="completed"，5 个 status="error"
- **THEN** success_rate 为 `0.95`

#### Scenario: P95 latency from span durations — 场景：从 span 持续时间计算的 P95 延迟
- **WHEN** 工具 spans 的持续时间为 [10ms, 20ms, 30ms, ..., 1000ms]
- **THEN** p95_latency_ms 是所有持续时间的第 95 百分位值

### Requirement: Frontend tool analytics dashboard — 需求：前端工具分析仪表板
系统应提供位于 `/ops-center/tools` 的工具分析页面，包含：概览卡片行（总执行次数、成功率、P95 延迟、错误计数）、每个工具的柱状图（按工具名称的成功率）、工具详细表（可按执行次数/延迟/错误率排序）以及顶级错误列表。页面应重用现有的 Recharts `BarChart` 和 `LineChart` 组件。

#### Scenario: Dashboard renders with data — 场景：仪表板渲染数据
- **WHEN** 用户导航到 `/ops-center/tools` 且工具 spans 存在
- **THEN** 概览卡片显示指标，柱状图显示每个工具的成功率，错误列表显示最近的失败

#### Scenario: Dashboard shows empty state — 场景：仪表板显示空状态
- **WHEN** 所选时间范围内没有工具 spans
- **THEN** 页面显示 "No data" 消息，并提供如何生成工具执行数据的指导

### Requirement: Ops Center sidebar navigation entry — 需求：Ops Center 侧边栏导航入口
侧边栏应包含一个 "Ops Center" 顶级导航项，链接到 `/ops-center/tools`。图标应使用 `lucide-react` 的 `LayoutDashboard` 或 `Gauge` 图标。

#### Scenario: Sidebar shows Ops Center entry — 场景：侧边栏显示 Ops Center 入口
- **WHEN** 仪表板侧边栏渲染
- **THEN** "Ops Center" 作为导航项出现，链接到 `/ops-center/tools`
