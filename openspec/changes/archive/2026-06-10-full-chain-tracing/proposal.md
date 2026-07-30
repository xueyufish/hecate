## Why — 原因

Hecate 有四个独立但互不关联的可观测性子系统（EventStore、TracingService 骨架、MetricsCollector 骨架、StructuredLogger 骨架），它们没有共享关联 ID。无法从 API 入口到 Service → Engine → Worker 再到返回，追踪单个用户请求。这使得在生产环境中调试代理行为、归因成本和监控延迟变得不可能。行业研究证实，8 个主要云平台中有 6 个（Google、AWS、Microsoft、Salesforce、IBM、阿里云）使用 OpenTelemetry 作为代理追踪的标准。

## What Changes — 变更内容

- 添加基于 OpenTelemetry 的追踪基础设施，新增 `opentelemetry-api` 和 `opentelemetry-sdk` 依赖
- 向 `EnginePort` 添加 `create_span` / `end_span` 抽象方法，使引擎层无需直接导入 OTel 即可创建 span
- 向 `EventStore.append()` 和 `Event` 数据类添加 `trace_id: str | None = None` 参数，将引擎事件与应用级追踪关联
- 将现有的 `TracingService` 骨架升级为基于新 `traces` ORM 表的生产实现（以观测为中心、带自引用 parent_id 的模型）
- 添加 `OpsTraceManager`——一个异步队列 + 提供者插件系统，将追踪写入本地数据库并可选择导出到外部提供者（LangFuse、OTel Collector 等）
- 在 `main.py` 中添加 `FastAPIInstrumentor` 中间件，为每个 HTTP 请求自动创建根 span 并通过 OTel `contextvars` 传播 `trace_id`
- 将 PregelRuntime 和 Workers 接入，通过 `EnginePort.create_span` 在执行边界（NODE_START/END、LLM_REQUEST/RESPONSE、TOOL_CALL/RESULT）创建 OTel span
- 添加 REST API 端点：`GET /api/traces`（列表）和 `GET /api/traces/{trace_id}`（带 span 树的详情）
- 为新的 `traces` 表添加 Alembic 迁移

## Capabilities — 能力

### New Capabilities — 新增能力
- `full-chain-tracing`：从 API 入口到引擎执行的端到端分布式追踪，包含 OTel 上下文传播、追踪持久化和查询 API

### Modified Capabilities — 修改的能力
- `engine-ports`：向 EnginePort ABC 添加 `create_span` 和 `end_span` 抽象方法，用于在引擎层创建 span 且无需直接依赖 OTel
- `eventstore`：向 Event 数据类和 `append()` 方法添加 `trace_id` 字段，用于将引擎事件与应用级追踪关联
- `core-infrastructure`：添加 OpenTelemetry SDK 依赖和 main.py 中的 FastAPI 中间件插装

## Impact — 影响

- **Dependencies**：新包 `opentelemetry-api`、`opentelemetry-sdk`、`opentelemetry-instrumentation-fastapi` 添加到 `pyproject.toml`（可能在新的 `[observability]` 可选依赖组下）
- **Engine layer**：`ports.py` 新增 2 个抽象方法；`eventstore.py` Event 数据类增加 `trace_id` 字段
- **Services layer**：`observability/tracing.py` 从骨架完全重写为生产实现；新增 `observability/trace_manager.py` 和 `observability/trace_providers.py` 文件
- **API layer**：新增 `api/management/traces.py` 路由；`main.py` 增加 OTel 中间件设置
- **Database**：通过 Alembic 迁移新增 `traces` 表
- **Tests**：针对 TracingService、OpsTraceManager、traces API 的新测试文件；针对新的 EnginePort 方法和 EventStore trace_id 参数的引擎测试更新
- **Breaking changes**：`EnginePort` ABC 新增抽象方法——所有实现（`_ProductionEnginePort`、`AgentExecutionPort`）必须更新。`Event` 数据类新增字段（有默认值，不完全破坏性，但影响冻结数据类的构造）
