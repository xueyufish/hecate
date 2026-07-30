## ADDED Requirements — 新增需求

### Requirement: Agent fleet overview endpoint — 需求：Agent 集群概览端点
系统应暴露 `GET /api/ops-center/agents/overview`，返回指定时间范围内的聚合集群健康数据：总 agent 数、按健康状态分布（healthy/warning/critical/unknown 计数）、集群级错误率、集群级平均 P95 延迟以及最差 agent 列表（按健康评分升序排列）。支持 `start_date`、`end_date` 查询参数。

#### Scenario: Fleet overview with mixed health statuses — 场景：混合健康状态的集群概览
- **WHEN** 客户端请求 `GET /api/ops-center/agents/overview?start_date=2026-07-01&end_date=2026-07-08`
- **THEN** 系统返回 `{total_agents, healthy_count, warning_count, critical_count, unknown_count, fleet_error_rate, fleet_p95_latency_ms, top_degraded: [{agent_id, agent_name, health_status, health_score, error_rate, p95_latency_ms}]}`

#### Scenario: Fleet overview with no active agents — 场景：无活跃 agent 的集群概览
- **WHEN** 客户端请求时间范围内不存在根追踪 spans 的集群概览
- **THEN** 系统返回 `{total_agents: 0, healthy_count: 0, warning_count: 0, critical_count: 0, unknown_count: 0, fleet_error_rate: 0.0, fleet_p95_latency_ms: 0.0, top_degraded: []}`

#### Scenario: Top degraded agents limited to 10 — 场景：最差 agent 限制为 10 个
- **WHEN** 超过 10 个 agent 的健康状况降级（warning 或 critical）
- **THEN** `top_degraded` 包含最多 10 个按 health_score 升序排列的条目

### Requirement: Per-agent health metrics endpoint — 需求：每个 agent 的健康指标端点
系统应暴露 `GET /api/ops-center/agents/{agent_id}/health`，返回特定 agent 的健康指标：总会话数、错误计数、错误率、成功率、平均会话延迟、P95 会话延迟、最后活跃时间戳、计算出的健康状态以及带有维度分解的计算出的健康评分。

#### Scenario: Health metrics for active agent — 场景：活跃 agent 的健康指标
- **WHEN** 客户端请求 `GET /api/ops-center/agents/{agent_id}/health?start_date=2026-07-01&end_date=2026-07-08`
- **THEN** 系统返回 `{agent_id, total_sessions, error_count, error_rate, success_rate, avg_latency_ms, p95_latency_ms, last_active_at, health_status, health_score, score_breakdown: {error_rate_dimension, latency_dimension, activity_dimension}}`

#### Scenario: Health metrics for agent with no activity — 场景：无活动 agent 的健康指标
- **WHEN** 客户端请求一个在时间范围内有零个根追踪 spans 的 agent 的健康信息
- **THEN** 系统返回 `{agent_id, total_sessions: 0, health_status: "unknown", health_score: null, ...}`，带有 null 评分和 unknown 状态

### Requirement: Health status taxonomy — 需求：健康状态分类
系统应将每个 agent 分类为四种健康状态之一：`healthy`、`warning`、`critical` 或 `unknown`。分类使用两个维度 — 错误率和 P95 会话延迟 — 每个都有可配置的 warning 和 critical 阈值。整体状态是最差维度状态（如果任一维度为 critical，则 agent 为 critical）。时间范围内有零个会话的 agent 被分类为 `unknown`。

#### Scenario: Healthy agent — 场景：健康 agent
- **WHEN** agent 的 error_rate ≤ warning 阈值（默认 5%）且 p95_latency ≤ warning 阈值（默认 10000ms）
- **THEN** agent 的 health_status 为 `healthy`

#### Scenario: Warning agent — high error rate only — 场景：警告 agent — 仅高错误率
- **WHEN** agent 的 error_rate > 5% 但 ≤ 15% 且 p95_latency ≤ 10000ms
- **THEN** agent 的 health_status 为 `warning`

#### Scenario: Critical agent — high latency — 场景：严重 agent — 高延迟
- **WHEN** agent 的 p95_latency > 30000ms（critical 阈值）
- **THEN** agent 的 health_status 为 `critical`，无论错误率如何

#### Scenario: Unknown agent — no activity — 场景：未知 agent — 无活动
- **WHEN** agent 在查询的时间范围内有零个根追踪 spans
- **THEN** agent 的 health_status 为 `unknown`

#### Scenario: Custom thresholds via configuration — 场景：通过配置的自定义阈值
- **WHEN** `AGENT_HEALTH_ERROR_RATE_WARNING` 设置为 0.03 且 agent 的错误率为 4%
- **THEN** agent 的 health_status 为 `warning`（4% > 3% 自定义阈值）

### Requirement: Configurable health score formula — 需求：可配置的健康评分公式
系统应使用跨三个维度的加权公式为每个 agent 计算健康评分（0-100）：错误率（默认权重 50%）、延迟（默认权重 30%）和活动（默认权重 20%）。权重通过 `AGENT_HEALTH_SCORE_WEIGHTS` 设置（JSON 对象）配置。维度评分：错误率维度 = `max(0, 100 - error_rate * 500)`、延迟维度 = `max(0, 100 - (p95_latency_ms / critical_threshold_ms) * 100)`、活动维度 = `min(100, session_count / 10 * 100)`（归一化到 10 会话基线）。状态为 unknown 的 agent 收到 `null` 评分。

#### Scenario: Perfect health score — 场景：完美健康评分
- **WHEN** agent 的错误率为 0%、p95 latency 为 1000ms（远低于 warning）、20 个会话
- **THEN** health_score 为 100（所有维度评分均为 100）

#### Scenario: Degraded score from high error rate — 场景：高错误率导致的降级评分
- **WHEN** agent 的错误率为 10%、延迟正常、活动正常
- **THEN** error_rate_dimension = max(0, 100 - 0.10 * 500) = 50，health_score = 50 * 0.5 + ~100 * 0.3 + ~100 * 0.2 = ~95（加权）

#### Scenario: Custom weights via configuration — 场景：通过配置的自定义权重
- **WHEN** `AGENT_HEALTH_SCORE_WEIGHTS` 设置为 `{"error_rate": 0.8, "latency": 0.1, "activity": 0.1}`
- **THEN** health_score 使用 80% 错误率权重、10% 延迟权重、10% 活动权重

#### Scenario: Unknown status yields null score — 场景：未知状态产生 null 评分
- **WHEN** agent 有零个会话（unknown 状态）
- **THEN** health_score 为 `null`

### Requirement: Per-agent health trends endpoint — 需求：每个 agent 的健康趋势端点
系统应暴露 `GET /api/ops-center/agents/{agent_id}/trends`，返回每日时间序列的健康指标：会话计数、错误计数、错误率、平均延迟和每日 P95 延迟。支持 `days` 参数（1-90，默认 7）和 `granularity` 参数（"daily"、"hourly"、"weekly"）。

#### Scenario: Daily trends for past 7 days — 场景：过去 7 天的每日趋势
- **WHEN** 客户端请求 `GET /api/ops-center/agents/{agent_id}/trends?days=7&granularity=daily`
- **THEN** 系统返回 `{date, total_sessions, errors, error_rate, avg_latency_ms, p95_latency_ms}` 条目列表，每天一个

#### Scenario: Empty trends for inactive agent — 场景：不活跃 agent 的空趋势
- **WHEN** agent 在请求期间没有追踪数据
- **THEN** 系统返回空列表 `[]`

### Requirement: Agent health data sourced from root trace spans — 需求：Agent 健康数据来源于根追踪 spans
系统应从 `type="trace"`（根会话 spans）的 TraceModel 记录中推导所有 agent 健康指标。每个根 span 代表一次完整的 agent 会话执行，包含 `agent_id`、`status`、`start_time` 和 `end_time`。系统应在所有查询中按 `~TraceModel.deleted` 过滤。

#### Scenario: Count sessions from root traces — 场景：从根追踪计数会话
- **WHEN** 计算 agent 的 total_sessions
- **THEN** 系统计数 `type="trace"` 且 `agent_id={agent_id}` 且 `start_time` 在范围内且 `deleted=false` 的 TraceModel 行

#### Scenario: Compute error rate from root trace status — 场景：从根追踪状态计算错误率
- **WHEN** 计算 agent 的 error_rate
- **THEN** 系统计数 `status="error"` 的行除以该 agent 在时间范围内的总行数

### Requirement: Configurable health thresholds via settings — 需求：通过设置的可配置健康阈值
系统应从应用程序设置中读取健康分类阈值：`AGENT_HEALTH_ERROR_RATE_WARNING`（默认 0.05）、`AGENT_HEALTH_ERROR_RATE_CRITICAL`（默认 0.15）、`AGENT_HEALTH_LATENCY_WARNING_MS`（默认 10000）、`AGENT_HEALTH_LATENCY_CRITICAL_MS`（默认 30000）和 `AGENT_HEALTH_SCORE_WEIGHTS`（默认 `{"error_rate": 0.5, "latency": 0.3, "activity": 0.2}`）。

#### Scenario: Default thresholds applied — 场景：应用默认阈值
- **WHEN** 未配置自定义健康设置
- **THEN** 系统使用 warning 错误率 5%、critical 错误率 15%、warning 延迟 10s、critical 延迟 30s

#### Scenario: Custom thresholds override defaults — 场景：自定义阈值覆盖默认值
- **WHEN** `AGENT_HEALTH_ERROR_RATE_CRITICAL` 设置为 0.10
- **THEN** 错误率 > 10% 的 agent 在错误率维度上被分类为 critical

### Requirement: Frontend agent health dashboard — 需求：前端 Agent 健康仪表板
系统应提供一个 React 仪表板页面 `/ops-center/agents`，显示：集群状态摘要卡片（healthy/warning/critical/unknown 计数，带有颜色编码徽章）、健康分布图、agent 集群表（可排序列：name、status、score、error rate、P95 latency、last active）以及指向每个 agent 详细信息视图的向下钻取链接。

#### Scenario: Fleet overview displayed on page load — 场景：页面加载时显示集群概览
- **WHEN** 用户导航到 `/ops-center/agents`
- **THEN** 页面获取 `GET /api/ops-center/agents/overview` 并显示状态摘要卡片和 agent 集群表

#### Scenario: Click agent row to view details — 场景：点击 agent 行查看详细信息
- **WHEN** 用户点击集群表中的 agent 行
- **THEN** 页面导航到 agent 详细信息视图，显示健康趋势图表和评分分解

#### Scenario: Time range filter — 场景：时间范围过滤器
- **WHEN** 用户选择不同的时间范围（例如，last 24h、last 7d、last 30d）
- **THEN** 页面使用更新的 `start_date` 和 `end_date` 参数重新获取概览数据

### Requirement: Sidebar navigation entry — 需求：侧边栏导航入口
系统应在侧边栏中现有的 "Ops Center" 部分下添加一个 "Agents" 子导航项，链接到 `/ops-center/agents`。

#### Scenario: Sidebar displays Agents link — 场景：侧边栏显示 Agents 链接
- **WHEN** 侧边栏渲染
- **THEN** 在 "Ops Center" 部分下，"Agents" 和 "Tools" 项都作为同级链接可见
