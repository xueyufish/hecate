## ADDED Requirements — 新增需求

### Requirement: HecateTraceSpanProcessor bridges OTel spans to TraceModel — 需求：HecateTraceSpanProcessor 将 OTel spans 桥接到 TraceModel
系统应实现 `HecateTraceSpanProcessor`，实现 OpenTelemetry `SpanProcessor` 接口（`on_start`、`on_end`、`shutdown`、`force_flush`）。在 span 开始时，处理器应创建一条 `TraceModel` 记录，包含 `start_time`、`name`、`type`（从 span 名称前缀推断）和 `metadata_`（来自 OTel 属性）。在 span 结束时，处理器应更新记录，包含 `end_time`、`status`（completed 或 error）、`output_data`（来自输出属性）和 `usage`（来自 usage 属性）。

#### Scenario: Tool span creates TraceModel with type="tool" — 场景：工具 span 创建 type="tool" 的 TraceModel
- **WHEN** ToolWorker 创建一个名为 `"tool:get_weather"` 的 span，属性为 `{"tool_name": "get_weather", "arguments": "{...}"}`
- **THEN** 处理器创建一条 TraceModel 记录，`type="tool"`、`name="tool:get_weather"`、`metadata_` 包含这些属性

#### Scenario: LLM span creates TraceModel with type="generation" — 场景：LLM span 创建 type="generation" 的 TraceModel
- **WHEN** LLMWorker 创建一个名为 `"llm:agent_node_1"` 的 span，属性为 `{"model": "gpt-4o", "message_count": 5}`
- **THEN** 处理器创建一条 TraceModel 记录，`type="generation"`、`name="llm:agent_node_1"`、`metadata_` 包含 model 和 message_count

#### Scenario: Root session span creates TraceModel with type="trace" — 场景：根会话 span 创建 type="trace" 的 TraceModel
- **WHEN** PregelRuntime 创建一个名为 `"session:{session_id}"` 的根 span
- **THEN** 处理器创建一条 TraceModel 记录，`type="trace"`、`parent_id=None`、`metadata_` 包含 `session_id` 和 `agent_id`

#### Scenario: Span end updates status and end_time — 场景：Span 结束更新 status 和 end_time
- **WHEN** 一个 span 正常结束，带有输出属性 `{"output.result_length": "42"}`
- **THEN** 处理器更新 TraceModel 记录，`status="completed"`、`end_time` 设置为当前时间、`output_data={"result_length": "42"}`

#### Scenario: Errored span sets status to error — 场景：出错 span 设置 status 为 error
- **WHEN** 一个 span 以异常结束，或带有输出属性 `{"output.error": "Connection refused"}`
- **THEN** 处理器更新 TraceModel 记录，`status="error"`，并在 `output_data` 中记录错误消息

### Requirement: Span type inference from name prefix — 需求：从名称前缀推断 span 类型
处理器应从 OTel span 名称中使用前缀匹配推断 TraceModel.type：`"tool:"` → `"tool"`、`"llm:"` 或 `"llm_stream:"` → `"generation"`、`"session:"` → `"trace"`。无法识别前缀的 span 应默认为 `type="span"`。

#### Scenario: Unrecognized prefix defaults to span — 场景：无法识别的前缀默认为 span
- **WHEN** 处理名为 `"custom_operation:data_sync"` 的 span
- **THEN** TraceModel 记录具有 `type="span"`

### Requirement: Async write queue with background consumer — 需求：带后台消费者的异步写入队列
处理器应使用 `asyncio.Queue` 缓冲 span 数据，并使用后台消费者任务通过异步 SQLAlchemy 批量写入 TraceModel 记录。队列应具有可配置的最大大小（`TRACE_DB_QUEUE_MAX_SIZE`，默认 10000）。当队列满时，新 spans 应被静默丢弃（带警告日志）以防止无限制的内存增长。

#### Scenario: Background consumer processes queued spans — 场景：后台消费者处理队列中的 spans
- **WHEN** 5 个 spans 入队并且后台消费者刷新
- **THEN** 5 条 TraceModel 记录批量持久化到数据库

#### Scenario: Queue full drops spans with warning — 场景：队列满时丢弃 spans 并发出警告
- **WHEN** 队列达到最大容量且创建新 span
- **THEN** span 不入队，记录 WARNING 日志，应用程序继续正常执行

#### Scenario: Application shutdown flushes queue — 场景：应用程序关闭时刷新队列
- **WHEN** 应用程序收到关闭信号
- **THEN** 处理器调用 `force_flush()`，在关闭前从队列中排空剩余的 spans

### Requirement: OTel trace_id stored in metadata — 需求：OTel trace_id 存储在 metadata 中
处理器应将 OTel trace ID（128 位十六进制字符串）和 span ID（64 位十六进制字符串）作为 `otel.trace_id` 和 `otel.span_id` 存储在 TraceModel 的 `metadata_` JSON 字段中。子 spans 应通过 OTel 上下文传播共享相同的 trace ID。

#### Scenario: Trace ID stored for cross-referencing — 场景：存储 Trace ID 用于交叉引用
- **WHEN** 处理具有 OTel trace_id `"0af7651916cd43dd8448eb211c80319c"` 的 span
- **THEN** TraceModel 记录的 `metadata_` 包含 `"otel.trace_id": "0af7651916cd43dd8448eb211c80319c"`

### Requirement: Processor registration in application startup — 需求：应用程序启动时注册处理器
系统应在应用程序启动时，当 `TRACING_ENABLED` 为 `True` 且 `TRACE_DB_EXPORT_ENABLED` 为 `True` 时，将 `HecateTraceSpanProcessor` 注册到 OTel `TracerProvider`。处理器应与现有的 `ConsoleSpanExporter` 处理器一起添加（而非替换）。

#### Scenario: Processor registered when tracing enabled — 场景：追踪启用时注册处理器
- **WHEN** 应用程序以 `TRACING_ENABLED=True` 和 `TRACE_DB_EXPORT_ENABLED=True` 启动
- **THEN** TracerProvider 同时注册了 `BatchSpanProcessor(ConsoleSpanExporter)` 和 `HecateTraceSpanProcessor`

#### Scenario: Processor not registered when DB export disabled — 场景：DB 导出禁用时不注册处理器
- **WHEN** `TRACE_DB_EXPORT_ENABLED=False`（测试默认值）
- **THEN** TracerProvider 只有控制台导出器，不执行 DB 写入

### Requirement: PregelRuntime creates root trace span — 需求：PregelRuntime 创建根 trace span
`PregelRuntime.execute()` 方法应在执行开始时创建一个名为 `"session:{session_id}"` 的根 OTel span，属性包括 `session.id` 和 `agent.id`（如果可用）。根 span 应使用 `tracer.start_as_current_span()` 创建，以便 Worker 的子 spans 通过 contextvars 自动嵌套。如果 span 创建失败，执行应继续正常进行，不进行追踪。

#### Scenario: Root span created for session execution — 场景：为会话执行创建根 span
- **WHEN** 使用 session_id=uuid 调用 PregelRuntime.execute()
- **THEN** 创建根 span "session:{session_id}" 并在执行期间设置为当前 OTel 上下文

#### Scenario: Child tool spans nest under root — 场景：子工具 spans 嵌套在根 span 下
- **WHEN** 在会话执行期间 ToolWorker 创建 span "tool:get_weather"
- **THEN** 工具 span 的父级是会话根 span，两者共享相同的 OTel trace_id

#### Scenario: Tracing failure does not break execution — 场景：追踪失败不会中断执行
- **WHEN** OTel tracer 不可用或 span 创建抛出异常
- **THEN** PregelRuntime.execute() 继续正常执行，记录 debug 消息，并产生正确结果

### Requirement: OpenTelemetry GenAI semantic convention attributes — 需求：OpenTelemetry GenAI 语义约定属性
工具 spans 应包含 `gen_ai.tool.name` 属性（来自工具名称）以及现有的自定义 `tool_name` 属性。LLM spans 应包含 `gen_ai.request.model` 属性（来自模型名称）以及现有的自定义 `model` 属性。这些标准属性支持与 OTel 兼容后端之间的互操作性。

#### Scenario: Tool span includes gen_ai.tool.name — 场景：工具 span 包含 gen_ai.tool.name
- **WHEN** ToolWorker 为工具 "get_weather" 创建 span
- **THEN** span 属性同时包含 `"tool_name": "get_weather"` 和 `"gen_ai.tool.name": "get_weather"`
