## ADDED Requirements — 新增需求

### 需求：使用以观测为中心的模型持久化追踪记录
系统应在 `traces` 表中持久化追踪和 span 数据，带有自引用 `parent_id` 外键。每条记录应包含字段：`id`（UUID PK）、`trace_id`（UUID，同一追踪内共享）、`parent_id`（UUID FK，可空）、`type`（varchar：trace、span、generation、tool、retrieval）、`name`、`session_id`、`agent_id`、`user_id`、`input_data`（JSONB）、`output_data`（JSONB）、`metadata`（JSONB）、`usage`（JSONB）、`level`（varchar）、`status`（varchar：started、completed、error）、`start_time`（timestamptz）、`end_time`（timestamptz）、`created_at`（timestamptz）

#### 场景：创建根追踪记录
- **当** 使用 `session_id`、`agent_id`、`name="chat_request"` 启动追踪
- **则** 应创建一条记录，其中 `parent_id=NULL`、`type="trace"`、`status="started"`，并自动生成 `id`、`trace_id`、`start_time`、`created_at`

#### 场景：在追踪下创建子 span
- **当** 使用 `parent_id=<root_trace_id>`、`name="llm_call"`、`type="generation"` 启动 span
- **则** 应创建一条记录，带有指定的 `parent_id`、与父级相同的 `trace_id`、`status="started"`

#### 场景：使用输出和使用数据完成 span
- **当** 使用 `span_id`、`output_data={"text": "response"}`、`usage={"input_tokens": 100, "output_tokens": 50}` 调用 `end_span`
- **则** 记录应更新为 `status="completed"`、`end_time=now()`，以及提供的输出和使用数据

### 需求：从 API 到引擎的 OTel 上下文传播
系统应使用 OpenTelemetry `contextvars` 将 `trace_id` 从 FastAPI 中间件传播到 Service → Engine → Worker 层。每个 HTTP 请求应通过 `FastAPIInstrumentor` 自动创建 OTel 根 span

#### 场景：为 HTTP 请求创建追踪上下文
- **当** POST 请求到达 `/api/sessions`
- **则** 应自动创建一个 OTel 根 span，`trace_id` 可从活动 span 上下文访问

#### 场景：在服务边界提取 Trace ID
- **当** 服务方法需要当前的 `trace_id`
- **则** 它应通过 `opentelemetry.trace.get_current_span().get_span_context().trace_id` 从活动 OTel span 上下文提取

#### 场景：无活动追踪上下文
- **当** 代码在 HTTP 请求之外运行（例如后台任务、CLI）
- **则** `trace_id` 应为 `None`，span 创建应为空操作（不抛出异常）

### 需求：带提供者插件的 OpsTraceManager 异步队列
系统应提供 `OpsTraceManager` 单例，通过异步队列将追踪事件分发到本地数据库持久化和可选的外部提供者

#### 场景：追踪写入本地数据库
- **当** 调用 `OpsTraceManager.on_span_end(span_data)`
- **则** span 数据应立即持久化到 `traces` 表

#### 场景：追踪分发到外部提供者
- **当** 配置了 `LangFuseProvider` 且 span 结束时
- **则** span 数据应排队以便异步分发到 LangFuse，不阻塞调用方

#### 场景：提供者失败不影响请求
- **当** 外部提供者在分发期间抛出异常
- **则** 应记录错误，但原始请求应正常完成

#### 场景：关闭时刷新待处理的追踪
- **当** 应用关闭时
- **则** 异步队列中所有待处理追踪事件应刷新到已配置提供者

### 需求：追踪查询 REST API
系统应暴露用于查询追踪数据的 REST API 端点

#### 场景：使用过滤器列出追踪
- **当** 调用 `GET /api/traces?session_id=<uuid>&agent_id=<uuid>&limit=20`
- **则** 应返回根追踪记录的分页列表，按 `start_time` 降序排序，包含字段：`trace_id`、`name`、`status`、`start_time`、`end_time`、`session_id`、`agent_id`、`usage` 摘要

#### 场景：获取带 span 树的追踪详情
- **当** 调用 `GET /api/traces/{trace_id}`
- **则** 应返回追踪根记录及其所有子 span 的分层树结构，包括每个 span 的 `input_data`、`output_data`、`metadata`、`usage`

#### 场景：按时间范围过滤追踪
- **当** 调用 `GET /api/traces?start_time=2026-01-01T00:00:00Z&end_time=2026-01-02T00:00:00Z`
- **则** 仅返回 `start_time` 在范围内的追踪

### 需求：PregelRuntime 和 Workers 中的 Span 创建
PregelRuntime 和 Workers 应通过 `EnginePort.create_span` 和 `EnginePort.end_span` 在执行边界创建 span

#### 场景：PregelRuntime 创建节点执行 span
- **当** PregelRuntime 开始执行节点
- **则** 应调用 `engine_port.create_span(name="node:{node_id}", attributes={"superstep": N})` 并将返回的 `span_id` 传递给相应的 `_emit` 调用

#### 场景：LLMWorker 创建 generation span
- **当** LLMWorker 调用 `llm_invoke`
- **则** 应创建 `type="generation"`、`name="llm:{model}"` 的 span，并在 span 结束时记录 `usage`（input_tokens、output_tokens）

#### 场景：ToolWorker 创建 tool span
- **当** ToolWorker 执行工具
- **则** 应创建 `type="tool"`、`name="tool:{tool_name}"` 的 span，并在 span 结束时记录 `output_data`

#### 场景：安全钩子创建防护栏 span
- **当** 安全钩子扫描输入/输出
- **则** 应创建 `type="span"`、`name="guardrail:{hook_name}"` 的 span，并在元数据中记录扫描结果
