## ADDED Requirements — 新增需求

### Requirement: Unified overview aggregation endpoint — 需求：统一概览聚合端点
系统应暴露 `GET /api/ops-center/overview`，聚合来自所有三个 Ops Center 子系统（Agent Health、Tool Analytics、Conversation Analytics）的指标为单个响应。端点应通过 `asyncio.gather(return_exceptions=True)` 并行调用三个现有服务。支持 `start_date` 和 `end_date` 查询参数。

#### Scenario: All three sources available — 场景：所有三个源都可用
- **WHEN** 客户端请求 `GET /api/ops-center/overview?start_date=...&end_date=...`
- **THEN** 系统返回 `{agent_health: {...}, tool_analytics: {...}, conversation_analytics: {...}, errors: []}`，包含所有三个子系统的数据

#### Scenario: One source fails — 场景：一个源失败
- **WHEN** ToolAnalyticsService 在聚合期间抛出异常
- **THEN** 系统返回 `{agent_health: {...}, tool_analytics: null, conversation_analytics: {...}, errors: ["tool_analytics: <error message>"]}`，HTTP 200（不是 500）

#### Scenario: All sources fail — 场景：所有源失败
- **WHEN** 所有三个服务抛出异常
- **THEN** 系统返回 `{agent_health: null, tool_analytics: null, conversation_analytics: null, errors: [...]}`，HTTP 200

### Requirement: Agent Health summary card — 需求：Agent 健康摘要卡片
概览应包括 agent 健康摘要：总 agent 数、healthy 计数、warning 计数、critical 计数、集群错误率和集群 P95 延迟。此数据来自 `AgentHealthService.get_fleet_overview()`。

#### Scenario: Agent health summary displayed — 场景：显示 Agent 健康摘要
- **WHEN** 概览端点返回 agent_health 数据
- **THEN** 前端显示卡片，展示总 agent 数、健康分布（healthy/warning/critical 计数及颜色编码徽章）、集群错误率和集群 P95 延迟

#### Scenario: Agent health data unavailable — 场景：Agent 健康数据不可用
- **WHEN** 概览响应中 agent_health 为 null
- **THEN** 前端显示 "Agent health data unavailable" 及重试指示器

### Requirement: Tool Analytics summary card — 需求：工具分析摘要卡片
概览应包括工具分析摘要：总执行次数、成功率、P95 延迟、错误计数和唯一工具数。此数据来自 `ToolAnalyticsService.get_overview()`。

#### Scenario: Tool analytics summary displayed — 场景：显示工具分析摘要
- **WHEN** 概览端点返回 tool_analytics 数据
- **THEN** 前端显示卡片，展示总执行次数、成功率百分比、P95 延迟和错误计数

#### Scenario: Tool analytics data unavailable — 场景：工具分析数据不可用
- **WHEN** 概览响应中 tool_analytics 为 null
- **THEN** 前端显示 "Tool analytics data unavailable" 及重试指示器

### Requirement: Conversation Quality summary card — 需求：对话质量摘要卡片
概览应包括对话质量摘要：总会话数、已评分会话数、平均质量评分和反馈比率（positive / total）。此数据来自 `ConversationAnalyticsService.get_overview()`。

#### Scenario: Conversation quality summary displayed — 场景：显示对话质量摘要
- **WHEN** 概览端点返回 conversation_analytics 数据
- **THEN** 前端显示卡片，展示总会话数、已评分会话数、平均质量评分和反馈比率百分比

#### Scenario: Conversation data unavailable — 场景：对话数据不可用
- **WHEN** 概览响应中 conversation_analytics 为 null
- **THEN** 前端显示 "Conversation data unavailable" 及重试指示器

### Requirement: Recent Activity Feed — 需求：最近活动源
概览应包括一个最近活动源，展示跨所有三个子系统的值得关注的事件：critical/warning agent、最近的工具错误和低质量对话。该源应按时间排序（最新的在前），最多 20 个项目。每个项目应有：时间戳、源（agent_health/tool_analytics/conversation_analytics）、严重性（critical/warning/info）、标题和指向相关子仪表板的链接。

#### Scenario: Activity feed with mixed events — 场景：混合事件的活动源
- **WHEN** 概览页面加载
- **THEN** 源显示按时间戳排序的最近事件：关键 agent、工具错误峰值、低质量对话 — 每个都有严重性徽章和详细链接

#### Scenario: No recent anomalies — 场景：无最近异常
- **WHEN** 所有子系统都健康（无关键 agent、无工具错误、无低质量对话）
- **THEN** 源显示 "All systems operational" 消息

#### Scenario: Activity feed limited to 20 items — 场景：活动源限制为 20 个项目
- **WHEN** 存在超过 20 个值得关注的事件
- **THEN** 源仅显示最近的 20 个项目

### Requirement: Quick links to sub-dashboards — 需求：指向子仪表板的快速链接
概览页面应显示指向三个子仪表板的快速链接按钮：Agent Health（`/ops-center/agents`）、Tool Analytics（`/ops-center/tools`）、Conversations（`/ops-center/conversations`）。每个链接应打开相应的子仪表板。

#### Scenario: Quick links displayed — 场景：显示快速链接
- **WHEN** 概览页面渲染
- **THEN** 显示三个链接卡片："View Agent Health"、"View Tool Analytics"、"View Conversations"

### Requirement: Time range selector — 需求：时间范围选择器
概览页面应包含一个时间范围选择器（Last 24h / 7d / 30d），使用更新的 `start_date` 和 `end_date` 参数重新获取概览数据。

#### Scenario: Default time range — 场景：默认时间范围
- **WHEN** 用户导航到概览页面
- **THEN** 默认时间范围为 "Last 7 days"，概览数据反映该范围

#### Scenario: Change time range — 场景：更改时间范围
- **WHEN** 用户选择 "Last 24h"
- **THEN** 页面使用 start_date = now - 24h 和 end_date = now 重新获取 `GET /api/ops-center/overview`

### Requirement: Sidebar overview link — 需求：侧边栏概览链接
侧边栏应有一个指向 `/ops-center`（新的概览页面）的 "Ops Center" 链接。现有的子仪表板链接（Agent Health、Tool Analytics、Conversations）应保持为同级链接。

#### Scenario: Ops Center link points to overview — 场景：Ops Center 链接指向概览
- **WHEN** 用户点击侧边栏中的 "Ops Center"
- **THEN** 浏览器导航到 `/ops-center`（统一概览页面）

#### Scenario: Sub-dashboard links remain accessible — 场景：子仪表板链接保持可访问
- **WHEN** 侧边栏渲染
- **THEN** "Agent Health"（`/ops-center/agents`）、"Tool Analytics"（`/ops-center/tools`）和 "Conversations"（`/ops-center/conversations`）链接作为 "Ops Center" 的同级链接可见

### Requirement: Empty state handling — 需求：空状态处理
概览页面应在所选时间范围内没有 Ops Center 数据时显示适当的空状态。

#### Scenario: No data in time range — 场景：时间范围内无数据
- **WHEN** 所选时间范围没有任何子系统的数据
- **THEN** 页面显示 "No Ops Center data available for this period" 及开始使用 agent 生成数据的指导
