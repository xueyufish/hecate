## MODIFIED Requirements — 修改的需求

### 需求：LLMWorker 在 LLM 调用前应用上下文管道

LLMWorker 应检查 execution_context 中的 ContextEngine 实例。当存在时，LLMWorker 应对从 channel_snapshot 提取的消息应用一个 5 步上下文管道，然后再传递给 `port.llm_invoke()`：

1. 工具结果截断：将每个工具结果内容限制在 `tool_result_limit` tokens（默认 2000）
2. Token 估算：调用 `context_engine.estimate_tokens(messages)`
3. 消息选择：如果估算的 tokens 超过预算，调用 `context_engine.select_messages(messages, budget)`
4. 上下文卸载：如果 `execution_context["context_offloader"]` 中有 `ContextOffloader` 可用，且丢弃的消息达到卸载阈值，则将丢弃的消息卸载到环境并替换为紧凑的引用桩
5. 压缩：如果 `[stub + selected]` 消息仍然超出预算，最后手段调用 `context_engine.compress(selected)`

管道应在 `execute()` 和 `execute_stream()` 两个方法中都应用。

#### 场景：存在 ContextEngine 时应用上下文管道
- **当** LLMWorker 收到包含 `"context_engine"` 的 execution_context 时
- **且** 消息列表超出 token 预算
- **则** LLMWorker 应应用消息选择以适配预算
- **且** 过滤后的消息应传递给 `port.llm_invoke()` 而非完整列表

#### 场景：ContextEngine 不存在时跳过上下文管道
- **当** LLMWorker 收到没有 `"context_engine"` 的 execution_context 时
- **则** LLMWorker 应像之前一样将完整消息列表传递给 `port.context_assemble()` 和 `port.llm_invoke()`
- **且** 不应发生过滤、选择、卸载或压缩

#### 场景：execute 和 execute_stream 都应用管道
- **当** ContextEngine 存在且消息超出预算时
- **则** `execute()` 和 `execute_stream()` 都应应用相同的上下文管道
- **且** 流式传输的 tokens 应对应于过滤后的消息，而非完整历史

#### 场景：卸载器可用时调用卸载步骤
- **当** execution_context 包含 `"context_offloader"` 及有效的 ContextOffloader 时
- **且** 消息选择丢弃的总 token 数至少达到 `CONTEXT_OFFLOAD_THRESHOLD_TOKENS`
- **则** 丢弃的消息应作为 JSON 文件卸载到环境
- **且** 紧凑的引用桩应替换活动上下文中被丢弃的块
- **且** 管道应在决定是否压缩之前在 `[stub + selected]` 上重新计算 token 数

#### 场景：卸载器不存在时跳过卸载
- **当** execution_context 不包含 `"context_offloader"` 时
- **且** 选择后消息仍超出预算
- **则** 管道应直接进入压缩（第 5 步）
- **且** 不应发生文件写入
- **且** 行为应与卸载前的 4 步管道完全一致

#### 场景：卸载不足时压缩触发
- **当** 卸载已发生（stub + selected）但 token 数仍然超出预算时
- **则** 管道应对 `[stub + selected]` 列表调用 `context_engine.compress()`
- **且** 压缩应从 `[stub + selected]` 列表中丢弃最旧的项目作为最后手段

### 需求：上下文管道是非破坏性的

上下文管道不应修改 channel 快照、channel 状态或 checkpoint 数据。过滤后的消息应是仅用于当前 LLM 调用的临时副本。channel 中的原始 `messages` 列表应保留所有消息。卸载的文件应是存储在环境中的额外工件 — 它们不会替换或删除 channel 的消息历史。

#### 场景：LLM 调用后 channel 消息不变
- **当** LLMWorker 应用上下文管道，将消息从 100 条过滤到 20 条时
- **且** WorkerResult 通过 `_apply_writes` 应用到 channels
- **则** channel `messages` 字段应包含原始的 100 条消息加上新的 assistant 消息
- **且** 不应有消息被上下文管道移除

#### 场景：Checkpoint 保留完整的消息历史
- **当** PregelRuntime 在应用上下文管道的超步后保存 checkpoint 时
- **则** checkpoint 应包含完整、未过滤的消息历史
- **且** 从此 checkpoint 恢复应提供对所有消息的访问

#### 场景：卸载不改变 channel 消息
- **当** 卸载步骤将丢弃的消息写入环境时
- **则** channel 的 `messages` 列表应保持不变
- **且** 卸载的文件应是存储在环境文件系统中的独立副本
