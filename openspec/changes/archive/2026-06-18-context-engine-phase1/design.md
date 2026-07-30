## Context — 背景

Hecate 的 ContextEngine ABC（engine/context.py）定义了三个方法——`select_messages`、`compress`、`estimate_tokens`，并有一个 InMemoryContextEngine 实现。然而，PregelRuntime 和 LLMWorker 目前都没有调用这些方法。LLMWorker 从通道快照中提取完整的 `messages` 列表，并通过透传的 `EnginePort.context_assemble()` 直接传递给 `port.llm_invoke()`。长对话会使 TOPIC `messages` 通道无限增长，没有任何 token 预算限制。

对 12 个平台（LangGraph、Google ADK、AutoGen、Semantic Kernel、Claude Code、AgentScope、IBM watsonx、Salesforce、openJiuwen、OpenAI Assistants、CrewAI、Microsoft Semantic Kernel）的研究表明，**零个平台**在图形执行器/运行时级别执行上下文过滤。所有平台都将其放置在以下位置之一：每个 agent 级别（AutoGen、Semantic Kernel、AgentScope）、预 LLM 流程级别（Google ADK、Claude Code、IBM watsonx）或用户定义的图形节点（LangGraph）。

现有的设计文档（archive/2026-06-02-context-engine-interface/design.md，决策 D1）指出："ContextEngine 是 engine 内部的，不在 EnginePort 上。EnginePort.context_assemble 仍然是公共 API；它在内部委托给 ContextEngine。"

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 将 ContextEngine 接入执行管道，使 LLM 调用接收受预算限制的消息
- PregelRuntime 拥有 ContextEngine 实例（与 Scheduler、Eviction、Optimization ABC 一致）
- LLMWorker 在 LLM 调用前应用多步上下文管道
- 非破坏性：通道状态、快照和检查点保持不变
- 向后兼容：`context_engine=None` 保留当前行为

**非目标：**
- 处理器链架构（第 2 阶段，功能 4.13，P4）
- 异步 ContextEngine 接口（当前为同步方法；基于 LLM 的生产级压缩是第 2 阶段）
- 来自模型注册表的每模型预算（第 2 阶段；第 1 阶段使用可配置的默认值）
- 上下文卸载/重新加载能力（第 2 阶段，受 AgentScope Offloader 启发）
- 基于轮次的窗口化（第 2 阶段，受 openJiuwen 启发）
- KV Cache 协调（第 2 阶段，受 openJiuwen 启发）
- 重构 ConversationService 以委托给 ContextEngine（单独变更）

## Decisions — 决策

### D1: PregelRuntime 作为组合根，LLMWorker 作为应用点

**选择**：PregelRuntime 通过构造函数参数接收 ContextEngine；LLMWorker 从 execution_context 中获取它，并在 `port.llm_invoke()` 之前应用它。

**理由**：所有 12 个研究的平台都将上下文过滤放置在 agent/worker 级别或预 LLM 流程级别——从不在图形执行器级别。PregelRuntime 的角色是状态管理和调度，而不是 LLM 提示工程。将 ContextEngine 放在 PregelRuntime 级别（修改快照）也会导致检查点被过滤后的消息破坏，造成永久性的信息丢失。

**考虑的替代方案**：
- **方案 A（PregelRuntime 修改快照）**：拒绝——没有行业先例；破坏检查点完整性；所有 12 个平台都避免这样做。
- **方案 C（EnginePort.context_assemble 委托给 ContextEngine）**：推迟——这是长期目标，但需要 services 层重构。第 1 阶段保持 context_assemble 不变，用于更高级别的上下文增强（记忆/知识注入），而 ContextEngine 在 LLMWorker 中处理低级别的消息选择/压缩。

### D2: LLMWorker 中的 4 步上下文管道

**选择**：LLMWorker 在 LLM 调用前应用四个顺序步骤：
1. **工具结果截断**——将过大的工具输出限制在 `tool_result_limit` 个 token 内（受 AgentScope 启发）
2. **Token 估算**——调用 `context_engine.estimate_tokens(messages)` 检查预算
3. **消息选择**——如果超出预算，调用 `context_engine.select_messages(messages, budget)`
4. **压缩**——如果选择后仍超出预算，调用 `context_engine.compress(selected)`

**理由**：Claude Code 的 5 级级联和 AgentScope 的三机制方法（压缩 + 截断 + 卸载）都证明，渐进式的、从低开销开始的干预比单步压缩更有效。第 1 阶段实现了一个简化的 4 步版本；第 2 阶段将演变为完整的处理器链。

### D3: 预算解析优先级

**选择**：Token 预算按以下顺序解析：
1. `node_config.get("max_tokens")`——每节点显式配置
2. `execution_context.get("context_budget")`——运行时全局预算
3. `8000`——常见模型的合理默认值

**理由**：AgentScope 使用 `trigger_ratio × model.context_length`，需要模型注册表集成。IBM watsonx 使用固定阈值（默认 20000）。第 1 阶段使用可配置默认值保持简单；第 2 阶段将添加来自模型注册表的每模型预算。

### D4: 非破坏性语义

**选择**：上下文管道创建消息的临时过滤副本。通道的 `messages` 字段、快照字典和所有检查点都保留完整、未过滤的消息历史。

**理由**：Claude Code L4（Context Collapse）使用非破坏性投影——原始消息从不修改，折叠决策在读取时重放。AgentScope 的 Offloader 将压缩后的内容保存到外部存储。AutoGen 的 ChatCompletionContext 返回只读视图。所有平台确保真相源（对话历史）永远不会被上下文过滤破坏。

### D5: execution_context 传递模式

**选择**：PregelRuntime 将 ContextEngine 注入到 `execution_context` 字典中（它已为 session_id、superstep、event_store、trace_id、event_bus 构建了每超步的上下文）。

**理由**：这遵循了代码库中已有的模式——EventStore 和 EventBus 已经通过 execution_context 传递。Worker 无需更改构造函数。向后兼容——不检查 ContextEngine 的 Worker 不受影响。

### D6: 工具结果限制默认值

**选择**：`tool_result_limit` 默认为 2000 个 token。可通过 `node_config.get("tool_result_limit")` 配置。

**理由**：AgentScope 默认为 1000 个 token。IBM watsonx 使用 50000 个 token 的 `large_message_threshold`。2000 是一个折中值——足够大以保留有用的工具输出，又足够小以防止单个工具结果占据上下文窗口。

## Risks / Trade-offs — 风险 / 权衡

| 风险 | 缓解措施 |
|------|------------|
| 同步 ContextEngine 方法无法进行基于 LLM 的摘要 | 第 1 阶段使用 InMemoryContextEngine（基于启发式，同步）。基于 LLM 的压缩推迟到第 2 阶段使用异步接口。 |
| Token 估算（4 字符/token）不准确 | 对预算检查足够好；第 2 阶段可以添加 tiktoken 或提供商特定的计数。InMemoryContextEngine 已经记录了此限制。 |
| 工具结果截断可能删除关键数据 | 截断保留前 N 个 token（输出的开头）。第 2 阶段的 Offloader 将允许检索完整输出。 |
| 只有 LLMWorker 受益；其他 Worker 不进行过滤 | LLMWorker 是唯一调用 `port.llm_invoke()` 的 Worker。Tool/Code worker 不需要上下文过滤。 |
| 没有每模型预算意味着默认 8000 对于大上下文模型来说可能太小 | 可通过 node_config 和运行时参数配置。用户可以设置适当的预算。第 2 阶段添加模型注册表集成。 |
