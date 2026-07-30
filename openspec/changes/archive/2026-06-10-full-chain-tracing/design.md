## Context — 上下文

Hecate 当前有四个断开的可观测性子系统：

1. **EventStore**（`engine/eventstore.py`）——仅追加的引擎事件日志，包含 12 种事件类型，已接入 PregelRuntime（8 处 emit 调用）和 Workers（4 处 emit 调用）。仅有内存实现
2. **TracingService**（`services/observability/tracing.py`）——完整的 TraceContext/SpanData API，但完全是内存骨架，从未在生产代码中调用
3. **MetricsCollector**（`services/observability/metrics.py`）——Prometheus 格式指标，每次请求重新实例化（无状态），从不累积
4. **StructuredLogger**（`services/observability/structured_logger.py`）——带上下文增强的 JSON 日志，功能完整但从未接入

没有将这些系统连接起来的 `trace_id`。请求进入 FastAPI，流经 Service → Engine → Worker，然后退出——跨层之间零关联。引擎层有严格的零外部依赖策略（仅 `jsonschema` 为例外），由 `EnginePort` 作为边界接口强制执行。

行业研究（Google Cloud、AWS、Microsoft Azure、Salesforce、IBM、阿里云）确认 OpenTelemetry 是代理平台追踪的事实标准。8 个主要平台中有 6 个使用 OTel 作为底层技术。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 建立一个从 FastAPI 中间件到 Service → Engine → Worker 层传播的单一 `trace_id`
- 将追踪/span 数据持久化到 PostgreSQL `traces` 表中，采用以观测为中心的设计
- 使 PregelRuntime 和 Workers 能够无需直接导入 OTel 即可创建 span（通过 EnginePort 抽象）
- 通过 `trace_id` 字段将现有 EventStore 事件与追踪关联
- 提供用于追踪列表和详情查询的 REST API
- 通过异步插件系统支持可选导出到外部提供者（LangFuse、OTel Collector）
- 保持引擎层零外部依赖的不变性

**非目标：**
- 实时追踪流式传输 / WebSocket 推送（P3 — 8.2 实时监控）
- 追踪 UI / 可视化仪表板（P3）
- 指标集成（从追踪数据生成 Prometheus 计数器/计量器）——保持 MetricsCollector 不变
- StructuredLogger 集成——暂时保持不变
- 跨多个 Hecate 实例的分布式追踪（W3C Trace Context 传播）——未来范围
- 基于追踪的自动评估或质量评分
- 追踪数据的保留/清理策略

## Decisions — 决策

### D1：EventStore trace_id——显式参数（非弱约定）

**决策**：向 `Event` 数据类和 `EventStore.append()` 添加 `trace_id: str | None = None` 作为显式参数

**理由**：`trace_id` 是追踪系统的核心关联字段。弱约定（将其放在 `payload` 字典中）允许调用者静默遗忘，破坏追踪完整性。显式参数确保编译时可见性和测试强制。影响是可管理的：`Event` 数据类增加一个带默认值的字段，`InMemoryEventStore.append()` 传递它，而 PregelRuntime/Workers 中所有约 12 处 emit 调用点从其执行上下文传递 `trace_id`

**考虑的替代方案**：
- 弱约定（payload 字典键）——已拒绝：没有编译时安全性，容易遗忘
- 独立的 `TracedEvent` 子类——已拒绝：不必要的类型扩散

### D2：引擎层 span 创建——通过 EnginePort 抽象方法

**决策**：向 `EnginePort` 添加 `create_span(name, parent_id=None, attributes=None)` 和 `end_span(span_id, output=None, usage=None)` 作为抽象方法。服务层适配器提供 OTel 实现

**理由**：EnginePort 的存在正是为了将引擎与外部依赖隔离。在引擎层添加 `opentelemetry-api` 将违反零外部依赖原则，并为进一步的依赖蔓延树立先例。两个现有实现（`engine_port_adapter.py` 中的 `_ProductionEnginePort`、`agent_execution_port.py` 中的 `AgentExecutionPort`）必须更新，但这是与 `llm_invoke`、`tool_execute` 等使用的相同模式

**考虑的替代方案**：
- 直接在引擎中导入 `opentelemetry-api`——已拒绝：违反架构不变性
- 通过构造函数注入传递 tracer 对象——已拒绝：将 OTel 类型泄漏到引擎签名中
- 回调函数模式——已拒绝：不如命名抽象方法可发现

### D3：追踪数据模型——以观测为中心的单一表

**决策**：单一 `traces` 表，带有 `parent_id` 自引用外键，遵循 LangFuse v4 以观测为中心的模型。每行是一个追踪根或子 span

**表结构**：
```
traces (
  id              UUID PK DEFAULT gen_random_uuid()
  trace_id        UUID NOT NULL           -- 同一追踪中所有记录共享
  parent_id       UUID FK → traces.id     -- 根 span 为 NULL
  type            VARCHAR(32) NOT NULL    -- 'trace', 'span', 'generation', 'tool', 'retrieval'
  name            VARCHAR(255) NOT NULL
  session_id      UUID                    -- 指向 sessions 的 FK（非会话追踪可为空）
  agent_id        UUID                    -- 指向 agents 的 FK
  user_id         UUID                    -- 来自认证上下文
  input_data      JSONB                   -- 请求/输入内容
  output_data     JSONB                   -- 响应/输出内容
  metadata        JSONB DEFAULT '{}'      -- 模型、延迟等
  usage           JSONB                   -- {input_tokens, output_tokens, cost_usd}
  level           VARCHAR(16) DEFAULT 'DEFAULT'  -- DEBUG, DEFAULT, WARNING, ERROR
  status          VARCHAR(16) DEFAULT 'started'   -- started, completed, error
  start_time      TIMESTAMPTZ NOT NULL
  end_time        TIMESTAMPTZ
  created_at      TIMESTAMPTZ DEFAULT now()
)
```

**索引**：`trace_id` 上的 `ix_traces_trace_id`，`session_id` 上的 `ix_traces_session_id`，`agent_id` 上的 `ix_traces_agent_id`，`parent_id` 上的 `ix_traces_parent_id`

**理由**：单一表带自引用比独立的追踪/span 表更简单。匹配 LangFuse v4 经过验证的模型。PostgreSQL 通过递归 CTE 对 span 树重构很好地处理分层查询

**考虑的替代方案**：
- 独立的 `traces` + `spans` 表——已拒绝：需要 JOIN，更复杂
- 无本地持久化，仅导出——已拒绝：需要外部提供者才能有任何可见性
- 仅追加事件日志（如 EventStore）——已拒绝：需要可变的 status/usage 字段

### D4：上下文传播——OTel contextvars

**决策**：使用 OpenTelemetry 内置的 `contextvars` 传播。`FastAPIInstrumentor` 为每个 HTTP 请求创建根 span。`trace_id` 从活动 OTel span 上下文中提取，并显式传递给 EnginePort 方法和 EventStore 调用

**理由**：这是行业标准（经 Google、AWS、Microsoft、Salesforce、IBM、阿里确认）。OTel contextvars 在 Python 3.12+ 中通过异步代码自动传播。异步层之间无需手动上下文传递——但对于引擎层，我们在服务边界从 contextvars 提取 `trace_id` 并通过方法参数显式传递，保持引擎对 OTel 的独立性

### D5：OpsTraceManager——带提供者插件的异步队列

**决策**：将 `OpsTraceManager` 实现为带异步队列的单例服务。追踪/span 写入同时发送到本地 `traces` 表（同步、立即）和可选的异步分发到已配置提供者

**提供者接口**：
```python
class TraceProvider(ABC):
    async def on_trace_start(self, trace: TraceRecord) -> None: ...
    async def on_span_start(self, span: SpanRecord) -> None: ...
    async def on_span_end(self, span: SpanRecord) -> None: ...
    async def flush(self) -> None: ...
```

**内置提供者**：`LangFuseProvider`、`OTelProvider`（发送 OTLP）。可通过插件注册添加额外提供者

**理由**：遵循 Dify `OpsTraceManager` 模式。异步分发确保追踪永远不会阻塞请求路径。本地持久化保证即使没有外部提供者也能可见

### D6：FastAPI 集成——最小化 OTel 插装

**决策**：使用 `opentelemetry-instrumentation-fastapi` 实现自动 HTTP 请求追踪。在 `main.py` 生命周期中配置。从请求状态添加自定义属性（`agent_id`、`session_id`、`user_id`）

**理由**：一行设置，API 层零手动 span 创建。HTTP span 的标准 OTel 属性。通过中间件或依赖注入添加自定义业务属性

## Risks / Trade-offs — 风险 / 权衡

- **[风险] 高 QPS 下的 PostgreSQL 追踪数据量** → 缓解措施：添加可配置的采样率（例如 10% 的追踪）。未来添加 TTL 清理任务。P2 阶段预计 <100 RPM，PostgreSQL 可以轻松处理
- **[风险] 新的 OTel 依赖体积** → 缓解措施：`opentelemetry-api` 和 `opentelemetry-sdk` 是轻量纯 Python 包。隔离在 `[observability]` 可选依赖组中，使核心安装保持精简
- **[风险] EnginePort ABC 扩展** → 缓解措施：仅 2 个新方法，遵循带默认值的可选方法的既有模式。两个实现已存在并将会更新
- **[风险] 追踪数据包含 PII（用户提示词、模型响应）** → 缓解措施：`input_data`/`output_data` 字段是可选的，可通过配置进行编辑。利用现有的安全层 `StreamSanitizer` 在追踪存储前删除敏感数据
- **[权衡] 单一 traces 表与独立 trace/span 表** → 已接受：更简单的模式，但 span 树查询需要递归 CTE。P2 规模下性能可接受
- **[权衡] 本地持久化 + 异步导出与仅导出** → 已接受：更多存储开销，但确保开箱即用的零依赖可观测性

## Open Questions — 未决问题

- 追踪数据是否应限定到工作区？当前设计使用 `session_id`/`agent_id` 进行过滤，但多租户工作区隔离可能需要 `workspace_id` 列
- 采样策略：固定百分比与自适应（基于错误率或延迟）？建议：从 100% 开始（记录所有内容），在 P3 中添加可配置采样
- 追踪保留策略：追踪数据保留多长时间？建议：默认 30 天，带可配置 TTL，作为未来的清理 cron 实现
