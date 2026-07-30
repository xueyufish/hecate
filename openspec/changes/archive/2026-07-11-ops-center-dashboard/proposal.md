## Why — 动机

Ops Center 有三个独立的子仪表板（Tool Analytics 8.9c、Agent Health 8.9a、Conversation Analytics 8.9b），但没有统一的入口点。操作员必须访问三个独立页面来了解整体系统健康状况。没有一个单一视图能够回答"我的 agent 集群健康吗、工具正常工作吗、对话质量好吗？"——这三个最重要的运维问题。

竞争平台（Salesforce Agentforce Command Center、IBM watsonx Runtime Monitoring、Palantir AIP Control Panel）都提供统一的"单一面板"，聚合来自所有子系统的运营指标。Hecate 需要同样的能力。

此变更（功能 8.9）是第四个也是最后一个 Ops Center 变更。它是一个聚合层 — 不创建新的数据源。它通过单个后端聚合端点（BFF 模式）消费三个现有的 Ops Center 服务（ToolAnalyticsService、AgentHealthService、ConversationAnalyticsService），并渲染一个统一的仪表板。

## What Changes — 变更内容

- **新增：`OpsCenterOverviewService`** — 后端聚合服务，并行扇出到三个现有的 Ops Center 服务（`asyncio.gather`），收集结果，并返回统一概览负载。优雅处理局部故障（失败源返回 `null` 及错误元数据）。
- **新增：REST API** — `GET /api/ops-center/overview` 返回所有三个子系统的聚合指标。单个端点、单个响应、为前端结构化的 JSON。
- **新增：前端概览页面** — 位于 `web/src/app/(dashboard)/ops-center/page.tsx` 的统一仪表板，显示三个摘要卡片（Agent Health、Tool Analytics、Conversation Quality）、跨源最近活动源和指向子仪表板的快速链接。
- **修改：侧边栏** — 将 "Ops Center" 链接从 `/ops-center/tools` 更改为 `/ops-center`（新的概览页面）。保持 Agent Health、Tool Analytics 和 Conversations 作为同级链接。
- **新增：最近活动源** — 跨所有三个子系统的时间排序值得关注的事件源：关键 agent、工具失败峰值、低质量对话。查询现有数据源 — 无新数据基础设施。

## Capabilities — 能力

### New Capabilities — 新增能力

- `ops-center-overview`：统一 Ops Center 仪表板，聚合来自 Agent Health（8.9a）、Tool Analytics（8.9c）和 Conversation Analytics（8.9b）的指标。后端 BFF 风格聚合端点，带局部故障处理。前端概览页面，包含摘要卡片、最近活动源和指向子仪表板的快速链接。

### Modified Capabilities — 修改的能力

（无 — 此变更引入新的聚合能力，不修改现有规范要求）

## Impact — 影响

- **Services 层**：`services/ops_center/` 中新增 `OpsCenterOverviewService`。遵循现有模式 — 构造函数接受 `AsyncSession`，并行调用现有服务。
- **API 层**：在现有的 `api/management/` 中添加新端点（独立路由器或添加到现有的 ops-center 路由器）。在 `main.py` 中注册。
- **前端**：新增 `ops-center/page.tsx` 概览页面。修改侧边栏链接目标。
- **配置**：无新设置 — 使用现有的时间范围默认值。
- **依赖**：无新包 — 重用现有的 FastAPI、SQLAlchemy、React 和图表库。
- **测试**：为 OpsCenterOverviewService 新增测试文件（聚合逻辑、局部故障处理）。
