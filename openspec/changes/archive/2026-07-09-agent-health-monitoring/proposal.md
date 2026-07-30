## Why — 动机

Hecate 的 TraceModel 现在填充了真实的执行数据（变更 1：`otel-trace-bridge-tool-analytics` 将 OTel spans 桥接到了 TraceModel），但操作员无法了解单个 agent 的健康状况。当某个 agent 的错误率飙升或延迟恶化时，没有仪表板可以展示问题 — 操作员必须手动查询追踪。竞争平台（Salesforce Agentforce、Microsoft Agent 365）提供集群级健康概览并支持向下钻取到单个 agent。Hecate 需要同样的能力。

此变更（功能 8.9a）是四个 Ops Center 变更中的第二个。它从 TraceModel 数据（变更 1 现在填充的数据）中推导出每个 agent 的健康指标，遵循与 `ToolAnalyticsService` 和 `ModelMonitoringService` 相同的 SQL 聚合模式。不需要新基础设施 — TraceModel 已经为每个执行 span 存储了 `agent_id`、`status`、`start_time`、`end_time` 和 `type`。

## What Changes — 变更内容

- **新增：`AgentHealthService`** — 聚合服务，查询 TraceModel 获取每个 agent 的健康指标：总执行次数、错误率、平均/P95 延迟、成功率、最后活跃时间戳和正常运行时间比率。遵循与 `ToolAnalyticsService` 相同的 SQL 查询模式。
- **新增：健康状态分类** — 三级分类（`healthy` / `warning` / `critical`），根据错误率和延迟的可配置阈值计算。每次查询时每个 agent 都会收到计算出的健康状态。
- **新增：可配置的健康评分公式** — 加权评分（0-100），结合错误率、延迟和活动。权重可通过设置配置。SLA 违规检测标记跨越阈值边界的 agent。
- **新增：集群概览** — 所有 agent 的聚合视图，显示健康状态分布（N healthy、N warning、N critical）、最差 agent 以及集群级错误/延迟趋势。
- **新增：每个 agent 的向下钻取** — 单个 agent 详细信息视图，包含时间序列趋势、最近的执行追踪和健康评分分解。
- **新增：REST API** — `GET /api/ops-center/agents/*` 端点，用于集群概览、每个 agent 的健康状况、趋势和告警。
- **新增：前端仪表板** — 位于 `web/src/app/(dashboard)/ops-center/agents/` 的 Agent 健康仪表板，包含集群状态卡片、健康分布图、带状态指示器的 agent 表以及向下钻取的详细信息视图。
- **新增：侧边栏子入口** — 在现有 "Ops Center" 部分下的 "Agents" 导航项。

## Capabilities — 能力

### New Capabilities — 新增能力

- `agent-health-monitoring`：基于 TraceModel 的每个 agent 健康监控。包括健康状态分类（healthy/warning/critical）、具有 SLA 违规检测的可配置健康评分公式、集群概览聚合、带趋势的每个 agent 向下钻取、REST API 和前端仪表板。

### Modified Capabilities — 修改的能力

（无 — 此变更引入新能力，不修改现有规范要求。它读取 TraceModel（由 `otel-trace-bridge` 填充）但不更改追踪行为。）

## Impact — 影响

- **Services 层**：`services/ops_center/` 中新增 `AgentHealthService`。遵循 `ToolAnalyticsService` 模式 — 在 TraceModel 上的纯 SQL 聚合查询。
- **API 层**：`api/management/agent_health.py` 中新增路由器。在 `main.py` 中注册。
- **配置**：新增健康阈值设置（`AGENT_HEALTH_ERROR_RATE_WARNING`、`AGENT_HEALTH_ERROR_RATE_CRITICAL`、`AGENT_HEALTH_LATENCY_P95_WARNING_MS`、`AGENT_HEALTH_LATENCY_P95_CRITICAL_MS`、`AGENT_HEALTH_SCORE_WEIGHTS`）。
- **前端**：新增 `ops-center/agents/` 页面 + 在 "Ops Center" 下的侧边栏子入口。
- **依赖**：无新包 — 重用现有的 SQLAlchemy async、TraceModel 和前端图表库。
- **测试**：为 AgentHealthService 和 API 端点新增测试文件。
