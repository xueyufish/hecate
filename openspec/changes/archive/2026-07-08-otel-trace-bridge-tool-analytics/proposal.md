## Why — 动机

Hecate 的 TraceModel 表已经存在，但从生产代码中接收不到任何数据。Worker（LLMWorker、ToolWorker）调用 `EnginePort.create_span()` 创建 OpenTelemetry spans，但这些 spans 仅通过 `ConsoleSpanExporter` 输出（或被静默丢弃）。专用的 `TracingService`（写入 TraceModel）已完全实现，但从未被任何业务代码路径实例化。结果，`signal_provider.ToolFailureRateProvider` 查询 `TraceModel.type == "tool"` 返回空结果，现有的 traces API（`GET /traces`）也不返回任何数据。不将 OTel spans 桥接到 TraceModel，整个可观测性层将无法正常工作。

此变更是 Sprint 6 Ops Center（功能 8.9a/b/c）的基础设施。它将 OTel spans 桥接到 TraceModel，使工具执行分析（8.9c）有真实数据可聚合。后续变更中的 Agent 健康（8.9a）和对话分析（8.9b）也将受益于填充的追踪数据。

## What Changes — 变更内容

- **新增：`HecateTraceSpanProcessor`** — 一个 OpenTelemetry `SpanProcessor`，拦截 span 生命周期事件（`on_start`、`on_end`）并将其持久化到 `TraceModel` 表中。使用异步队列 + 后台消费者模式（与现有 `AuditBatchWriter` 相同）桥接同步 OTel SDK 与异步 SQLAlchemy。
- **新增：PregelRuntime 根 trace span** — `PregelRuntime.execute()` 在每次会话执行开始时创建根 OTel span，实现完整的追踪树层次结构。Worker 的子 spans（LLM 调用、工具调用）通过 OTel 上下文传播（asyncio 中的 contextvars）自动嵌套在根 span 下。
- **新增：`ToolAnalyticsService`** — 聚合服务，查询 TraceModel 获取每个工具的指标：成功率、P95 延迟、顶级错误、执行趋势以及按 agent/会话的向下钻取。
- **新增：REST API** — `GET /api/ops-center/tools/*` 端点，用于工具分析概览、每个工具的详细信息、趋势和顶级错误。
- **新增：前端页面** — 位于 `web/src/app/(dashboard)/ops-center/tools/` 的工具分析仪表板，包含成功率柱状图、延迟表和错误列表。
- **新增：侧边栏入口** — "Ops Center" 顶级导航项。
- **采用：OpenTelemetry GenAI 语义约定** — 工具 spans 除了现有的自定义属性外，还使用 `gen_ai.tool.name`、`gen_ai.tool.type` 属性，与行业标准（Bedrock AgentCore、AgentScope、Dify 都使用 OTel 原生检测）保持一致。

## Capabilities — 能力

### New Capabilities — 新增能力

- `otel-trace-bridge`：OpenTelemetry SpanProcessor，将 OTel spans 桥接到 TraceModel。包括 span 到 TraceModel 字段映射（从 span 名称前缀推断类型、属性到元数据的映射）、具有后台消费者的异步写入队列，以及在应用程序启动时注册。PregelRuntime 根 span 创建，实现完整追踪树层次结构。
- `tool-execution-analytics`：基于 TraceModel 数据的每个工具执行分析。聚合查询成功率、P95 延迟、错误模式、趋势以及按 agent/会话的向下钻取。REST API 和前端仪表板。

### Modified Capabilities — 修改的能力

（无 — 此变更引入新能力，不修改现有规范要求）

## Impact — 影响

- **Engine 层**：`PregelRuntime.execute()` 增加了根 span 包装器（约 10 行，try/except 保护）。Worker 或 WorkerPool 接口无变化。
- **Services 层**：`services/` 中新增 `ToolAnalyticsService`。`services/observability/` 中新增 `HecateTraceSpanProcessor`。
- **API 层**：`api/management/tool_analytics.py` 中新增路由器。在 `main.py` 中注册。
- **配置**：新增设置（`TRACE_DB_EXPORT_ENABLED`、`TRACE_DB_QUEUE_MAX_SIZE`、`TRACE_DB_FLUSH_INTERVAL`）。
- **前端**：新增 `ops-center/tools/` 页面 + 侧边栏入口。
- **依赖**：无新包 — 使用 pyproject.toml 中已有的 `opentelemetry-sdk` 和 `opentelemetry-api`。
- **测试**：为 SpanProcessor、ToolAnalyticsService 和 API 端点新增测试文件。
