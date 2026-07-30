## ADDED Requirements — 新增需求

### Requirement: PregelRuntime accepts optional ContextEngine parameter — PregelRuntime 接受可选的 ContextEngine 参数

PregelRuntime SHALL 接受可选的 `context_engine: ContextEngine | None` 构造函数参数。当提供时，PregelRuntime SHALL 在每次超步调度时通过 `execution_context["context_engine"]` 将其传递给 Worker。

#### Scenario: PregelRuntime with ContextEngine — 带 ContextEngine 的 PregelRuntime

- **WHEN** PregelRuntime 使用 ContextEngine 实例构造
- **THEN** 传递给 Worker 的 execution_context 字典 SHALL 包含键 `"context_engine"`，其值为 ContextEngine 实例

#### Scenario: PregelRuntime without ContextEngine (backward compatible) — 不带 ContextEngine 的 PregelRuntime（向后兼容）

- **WHEN** PregelRuntime 在没有 context_engine 参数（或使用 None）的情况下构造
- **THEN** execution_context 字典 SHALL NOT 包含键 `"context_engine"`
- **AND** 所有现有行为 SHALL 保持不变

### Requirement: LLMWorker applies context pipeline before LLM invocation — LLMWorker 在 LLM 调用前应用上下文管道

LLMWorker SHALL 检查 execution_context 中的 ContextEngine 实例。当存在时，LLMWorker SHALL 对从 channel_snapshot 提取的消息应用 4 步上下文管道，然后再传递给 `port.llm_invoke()`：

1. 工具结果截断：将每个工具结果内容限制为 `tool_result_limit` 个 token（默认 2000）
2. Token 估算：调用 `context_engine.estimate_tokens(messages)`
3. 消息选择：如果估算的 token 超出预算，调用 `context_engine.select_messages(messages, budget)`
4. 压缩：如果选择的消息仍然超出预算，调用 `context_engine.compress(selected)`

该管道 SHALL 在 `execute()` 和 `execute_stream()` 方法中都应用。

#### Scenario: Context pipeline applied when ContextEngine is present — ContextEngine 存在时应用上下文管道

- **WHEN** LLMWorker 收到包含 `"context_engine"` 的 execution_context
- **AND** 消息列表超出 token 预算
- **THEN** LLMWorker SHALL 应用消息选择以适应预算
- **AND** 过滤后的消息 SHALL 传递给 `port.llm_invoke()` 而不是完整列表

#### Scenario: Context pipeline skipped when ContextEngine is absent — ContextEngine 不存在时跳过上下文管道

- **WHEN** LLMWorker 收到不包含 `"context_engine"` 的 execution_context
- **THEN** LLMWorker SHALL 像以前一样将完整消息列表传递给 `port.context_assemble()` 和 `port.llm_invoke()`
- **AND** 不进行过滤、选择或压缩

#### Scenario: Both execute and execute_stream apply pipeline — execute 和 execute_stream 都应用管道

- **WHEN** ContextEngine 存在且消息超出预算
- **THEN** `execute()` 和 `execute_stream()` SHALL 应用相同的上下文管道
- **AND** 流式 token SHALL 对应于过滤后的消息，而不是完整历史

### Requirement: Context pipeline is non-destructive — 上下文管道是非破坏性的

上下文管道 SHALL NOT 修改通道快照、通道状态或检查点数据。过滤后的消息 SHALL 仅用于当前 LLM 调用的临时副本。通道中的原始 `messages` 列表 SHALL 保留所有消息。

#### Scenario: Channel messages unchanged after LLM call — LLM 调用后通道消息不变

- **WHEN** LLMWorker 应用上下文管道，将消息从 100 条过滤到 20 条
- **AND** WorkerResult 通过 `_apply_writes` 应用到通道
- **THEN** 通道 `messages` 字段 SHALL 包含原始的 100 条消息加上新的 assistant 消息
- **AND** 没有消息被上下文管道删除

#### Scenario: Checkpoint retains full message history — 检查点保留完整消息历史

- **WHEN** PregelRuntime 在应用了上下文管道的超步后保存检查点
- **THEN** 检查点 SHALL 包含完整、未过滤的消息历史
- **AND** 从该检查点恢复 SHALL 提供对所有消息的访问

### Requirement: Token budget resolution priority — Token 预算解析优先级

消息选择的 token 预算 SHALL 按以下优先级顺序解析：

1. `node_config.get("max_tokens")`——每节点显式配置
2. `execution_context.get("context_budget")`——运行时全局预算
3. `8000`——默认预算

#### Scenario: Per-node budget takes priority — 每节点预算优先

- **WHEN** node_config 包含 `"max_tokens": 16000`
- **AND** execution_context 包含 `"context_budget": 8000`
- **THEN** 用于消息选择的预算 SHALL 是 16000

#### Scenario: Runtime budget used when no per-node config — 无每节点配置时使用运行时预算

- **WHEN** node_config 不包含 `"max_tokens"`
- **AND** execution_context 包含 `"context_budget": 12000`
- **THEN** 用于消息选择的预算 SHALL 是 12000

#### Scenario: Default budget when no config — 无配置时使用默认预算

- **WHEN** node_config 不包含 `"max_tokens"`
- **AND** execution_context 不包含 `"context_budget"`
- **THEN** 用于消息选择的预算 SHALL 是 8000

### Requirement: Tool result truncation before message selection — 消息选择前的工具结果截断

在消息选择之前，LLMWorker SHALL 截断内容超过 `tool_result_limit` 个 token（默认 2000，可通过 `node_config.get("tool_result_limit")` 配置）的单个工具结果消息。截断 SHALL 保留工具结果内容的前 N 个 token 并附加截断指示符。

#### Scenario: Oversized tool result truncated — 过大的工具结果被截断

- **WHEN** 工具结果消息包含 5000 个 token 的内容
- **AND** tool_result_limit 为 2000
- **THEN** 工具结果内容 SHALL 被截断到大约 2000 个 token
- **AND** SHALL 附加截断指示符以指示内容被移除

#### Scenario: Small tool result preserved — 小的工具结果被保留

- **WHEN** 工具结果消息包含 500 个 token 的内容
- **AND** tool_result_limit 为 2000
- **THEN** 工具结果内容 SHALL 保持不变
