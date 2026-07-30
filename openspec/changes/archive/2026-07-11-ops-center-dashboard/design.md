## Context — 背景

Ops Center 在变更 1-3 中构建了三个子仪表板：

- **ToolAnalyticsService**（`services/ops_center/tool_analytics.py`）— `get_overview()`、`get_tool_details()`、`get_trends()`、`get_top_errors()`。API 位于 `/api/ops-center/tools/*`。
- **AgentHealthService**（`services/ops_center/agent_health.py`）— `get_fleet_overview()`、`get_agent_health()`、`get_agent_trends()`。API 位于 `/api/ops-center/agents/*`。
- **ConversationAnalyticsService**（`services/ops_center/conversation_analytics.py`）— `get_overview()`、`get_quality_distribution()`、`get_topics()`、`get_low_quality()`、`get_conversation_turns()`、`get_trends()`。API 位于 `/api/ops-center/conversations/*`。

所有三个服务遵循相同的模式：构造函数接受 `AsyncSession`、方法返回 `dict[str, Any]`、查询使用 SQLAlchemy `func.count()` / `func.avg()` 并带 `~Model.deleted` 过滤器。

侧边栏目前在 "Ops Center" 下有 3 个同级链接：Tools（`/ops-center/tools`）、Agent Health（`/ops-center/agents`）、Conversations（`/ops-center/conversations`）。"Ops Center" 链接本身指向 `/ops-center/tools`。

**行业研究（Salesforce、Palantir、BFF 模式）：**
- Salesforce Agentforce 使用"语义数据模型（SDM）"作为统一的后端真相来源；Command Center UI 消费聚合数据，而非原始服务调用。
- Palantir AIP 使用三层架构：数据/本体 → 逻辑 → 应用/仪表板。仪表板从不直接调用多个服务。
- BFF（Backend for Frontend）模式："聚合：并行扇出到下游服务。重塑：为特定 UI 转换数据。优雅处理局部故障。"

## Goals / Non-Goals — 目标 / 非目标

**Goals（目标）：**

- 单个后端聚合端点（`GET /api/ops-center/overview`），并行扇出到所有三个 Ops Center 服务
- 局部故障处理：如果一个服务失败，该部分返回 `null` 及错误元数据（而非 500）
- 统一仪表板页面，包含来自所有三个子系统的摘要卡片
- 跨源最近活动源（关键 agent、工具失败、低质量对话）
- 指向子仪表板的快速链接
- 侧边栏 "Ops Center" 链接指向新的概览页面

**Non-Goals（非目标）：**

- 自定义仪表板构建器（O8） — 计划作为 P4+ 增强，独立于此变更
- 基于角色的仪表板个性化 — 未来增强
- 实时 WebSocket 更新 — 仅 REST 轮询（与子仪表板相同）
- 新数据源（告警、审计、成本） — 仅聚合现有的 8.9a/b/c 数据
- 侧边栏层次结构重组 — 保持平面同级链接，只更改 "Ops Center" 目标

## Decisions — 决策

### Decision 1: Backend aggregation (BFF pattern, not frontend parallel fetch) — 决策 1：后端聚合（BFF 模式，而非前端并行获取）

**选择**：单个 `GET /api/ops-center/overview` 端点，通过 `asyncio.gather(return_exceptions=True)` 并行调用所有三个服务。

**理由**：行业标准（Salesforce SDM、Palantir 本体查询、BFF 模式）。关键优势：
- 局部故障处理：后端为失败源返回 `null`，前端渲染降级状态
- 响应塑形：后端将每个服务的响应修剪为仅概览卡片需要的内容
- 缓存：后端级别 30-60 秒 TTL（未来增强，非阻塞）
- 延迟：一个 HTTP 请求对比三个（PayPal 测量 p99 每个往返 ≥700ms）

**考虑的替代方案**：
- **前端 `Promise.all`**：零后端工作，但没有局部故障处理，3 倍 HTTP 延迟，无缓存。BFF 研究明确警告不要用于仪表板。
- **GraphQL**：3 个数据源过度设计。增加操作复杂性。

### Decision 2: Only aggregate existing 8.9a/b/c data — 决策 2：仅聚合现有的 8.9a/b/c 数据

**选择**：概览页面仅显示来自 Agent Health、Tool Analytics 和 Conversation Analytics 的指标。不包含告警、审计日志、成本趋势或部署状态。

**理由**：路线图明确将 8.9 定义为"在 8.9a/b/c 数据源之上的聚合层。"添加新数据源会将范围从 M 扩展到 L。这些系统有自己的仪表板。

**考虑的替代方案**：
- **包含告警/审计**：需要查询 AlertService 和 AuditMiddleware。范围蔓延。它们有自己的 UI。

### Decision 3: Flat sidebar with updated link target — 决策 3：带更新链接目标的平面侧边栏

**选择**：在 "Ops Center" 部分下保持 4 个同级链接。将 "Ops Center" 链接从 `/ops-center/tools` 更改为 `/ops-center`（新的概览页面）。无需可折叠树结构。

**理由**：Salesforce Agentforce Studio 使用平面标签（Analytics、Optimization、Health、Testing Center），而非可折叠树。当前侧边栏是平面列表 — 为 4 个链接实现可折叠部分是不必要的复杂性。

**考虑的替代方案**：
- **可折叠的侧边栏部分**：更多导航层次，但增加了 UI 复杂性（状态管理、展开/折叠动画），收益甚微。

### Decision 4: Recent Activity Feed from existing queries — 决策 4：来自现有查询的最近活动源

**选择**：通过查询每个子系统的最近异常构建跨源活动源：
- Agent Health：`health_status = "critical"` 或 `"warning"` 的 agent
- Tool Analytics：有最近错误峰值（来自 `get_top_errors`）的工具
- Conversation Analytics：`quality_score < 0.5` 的对话

合并并按时间戳排序。无新数据模型 — 重用现有查询。

**理由**：操作员需要一个"需要关注什么"的单一源，而不是检查 3 个仪表板。这是统一仪表板中价值最高的功能。Salesforce 有类似的概念（"标记低绩效主题和广泛的配置差距"）。

## Risks / Trade-offs — 风险 / 权衡

- **[风险] 聚合延迟** → 三个并行服务调用。如果一个慢，总延迟 = 最慢服务。缓解措施：每个服务查询都已索引且快速（<100ms）。总延迟应 <300ms。未来：添加每次调用超时并返回部分结果。
- **[风险] 局部故障混淆** → 如果 ToolAnalytics 失败，概览显示 `null` 为该卡片。缓解措施：显示"数据不可用"及重试按钮，而不是错误页面。
- **[权衡] v1 中无缓存** → 每个概览请求触发 3 个服务调用。对于低流量管理仪表板可接受。未来：添加带 60 秒 TTL 的 Redis 缓存。
- **[权衡] 固定布局** → 无小部件自定义。可接受 — 自定义仪表板构建器（O8）是未来增强。
