## MODIFIED Requirements — 修改后的需求

### Requirement: Span creation in PregelRuntime and Workers — 需求：PregelRuntime 和 Workers 中的 Span 创建
PregelRuntime and Workers SHALL create spans at execution boundaries via `EnginePort.create_span` and `EnginePort.end_span`. LLMWorker SHALL additionally instrument time-to-first-token (TTFT) for streaming LLM calls by recording `ttft_ms` in the span's metadata.

PregelRuntime 和 Workers 应通过 `EnginePort.create_span` 和 `EnginePort.end_span` 在执行边界创建 span。LLMWorker 还应额外为流式 LLM 调用记录首 token 时间（TTFT），将 `ttft_ms` 记录在 span 的元数据中。

#### Scenario: PregelRuntime creates node execution span — 场景：PregelRuntime 创建节点执行 span
- **WHEN** PregelRuntime starts executing a node
- **THEN** it SHALL call `engine_port.create_span(name="node:{node_id}", attributes={"superstep": N})` and pass the returned `span_id` to the corresponding `_emit` call

- **当** PregelRuntime 开始执行节点
- **则**应调用 `engine_port.create_span(name="node:{node_id}", attributes={"superstep": N})` 并将返回的 `span_id` 传递给对应的 `_emit` 调用

#### Scenario: LLMWorker creates generation span — 场景：LLMWorker 创建生成 span
- **WHEN** LLMWorker calls `llm_invoke`
- **THEN** it SHALL create a span with `type="generation"`, `name="llm:{model}"`, and record `usage` (input_tokens, output_tokens) on span end

- **当** LLMWorker 调用 `llm_invoke`
- **则**应创建 `type="generation"`、`name="llm:{model}"` 的 span，并在 span 结束时记录 `usage`（input_tokens、output_tokens）

#### Scenario: LLMWorker records TTFT for streaming responses — 场景：LLMWorker 为流式响应记录 TTFT
- **WHEN** LLMWorker processes a streaming LLM response and receives the first chunk
- **THEN** it SHALL record `ttft_ms` (milliseconds from request start to first chunk arrival) in the span's `metadata` field

- **当** LLMWorker 处理流式 LLM 响应并收到第一个数据块
- **则**应在 span 的 `metadata` 字段中记录 `ttft_ms`（从请求开始到第一个数据块到达的毫秒数）

#### Scenario: ToolWorker creates tool span — 场景：ToolWorker 创建工具 span
- **WHEN** ToolWorker executes a tool
- **THEN** it SHALL create a span with `type="tool"`, `name="tool:{tool_name}"`, and record `output_data` on span end

- **当** ToolWorker 执行工具
- **则**应创建 `type="tool"`、`name="tool:{tool_name}"` 的 span，并在 span 结束时记录 `output_data`

#### Scenario: Security hook creates guardrail span — 场景：安全钩子创建护栏 span
- **WHEN** a security hook scans input/output
- **THEN** it SHALL create a span with `type="span"`, `name="guardrail:{hook_name}"`, and record scan result in metadata

- **当**安全钩子扫描输入/输出
- **则**应创建 `type="span"`、`name="guardrail:{hook_name}"` 的 span，并在元数据中记录扫描结果
