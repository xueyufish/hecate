## Context — 上下文

Hecate 有三种 agent 执行模式（`chat`、`three_layer`、`workflow`），但只有 `workflow` 模式在生产环境中使用 PregelRuntime 图引擎。Chat 模式完全绕过引擎，通过 ConversationService 的约 700 行命令式编排循环来处理上下文组装、记忆、知识检索、工具调用、流式输出和建议——所有这些都在一个单体方法中完成。Three_layer 模式有一个图模板（`templates.py` 中的 `build_three_layer_graph()`），但只有测试运行器通过 PregelRuntime 调用它。

引擎层已经统一——`templates.py` 证明了 three_layer 只是一个 GraphConfig。不一致之处在于 services/api 层，chat.py 直接调用 ConversationService，后者直接调用 LLMService，完全绕过了图引擎。

ConversationService 编排的子服务已经分解得很好：ContextAssembler、BudgetManager、WorkingMemoryService、UserMemoryService、CompressionPipeline、knowledge_base_service、llm_service、SuggestionService、EvidenceTracker。它们不需要重写——它们需要被 Workers 调用，而不是被 ConversationService 调用。

## Goals / Non-Goals — 目标/非目标

**目标：**
- 所有三种 agent 模式（chat、three_layer、workflow）都通过 PregelRuntime 执行
- ConversationService 的编排逻辑被图边和 Workers 替代
- 生产级 Workers 替代所有节点类型的 `_TestWorker`
- Guardrail Hooks（PreLLMHook、PostLLMHook、PreToolHook、PostToolHook）作为横切安全机制集成到 Workers 中——所有模式都获得防护保护
- 基于 Token 级别的 SSE 流式输出通过 PregelRuntime 的 yield 机制（`StreamMode.MESSAGES`）工作
- 现有的子服务（ContextAssembler、MemoryServices、LLMService 等）保持不变
- Chat API（`POST /v1/chat/completions`）的行为保持不变——相同的输入，相同的输出

**非目标：**
- 对话式 workflow 模式（1.1.8——这是下一次变更，建立在此之上）
- StreamMode.DEBUG 实现（P2 但不在本次变更中）
- 数据库支持的 CheckpointStore（P3）
- three_layer 模式 UI 配置（基于 canvas 的 three_layer 编辑）
- 针对聊天模式的 PregelRuntime 开销性能优化

## Decisions — 决策

### Decision 1：Worker 粒度——每个 NodeType 一个 Worker，而非每个服务一个

**选择**：每个 NodeType 有一个专用的 Worker 类，内部调用相应的服务。

**理由**：ConversationService 内部做了 12 件事（上下文组装、记忆加载、知识检索、LLM 调用、工具执行、证据跟踪、流式输出、建议、开场白、事实提取、provider 塑造、压缩）。如果每件事都做一个单独的 NodeType，即使是简单的聊天也需要多节点图，增加了不必要的复杂性。相反，每个 NodeType 的 Worker 在其领域内内部处理。

映射：
- `CONVERSATION` → `_LLMWorker`：内部调用 PreLLMHook、ContextAssembler、MemoryServices、LLMService、provider 塑造、压缩、PostLLMHook、证据跟踪
- `TOOL_CALL` → `_ToolWorker`：调用 PreToolHook、工具注册表、PostToolHook、证据跟踪
- `CONDITION` → `_ConditionWorker`：根据通道状态评估表达式
- `AGENT` → `_AgentWorker`：调用 EnginePort.agent_execute 进行子 agent 委派
- `KNOWLEDGE_RETRIEVAL` → `_KnowledgeWorker`：调用 knowledge_base_service
- `VARIABLE_SET` → `_VariableSetWorker`：向通道写入值

**考虑的替代方案**：每个服务一个 Worker（12 个 Workers）。被拒绝，因为即使是简单的聊天模式也需要复杂的多节点图。

### Decision 2：ConversationNode 上下文加载策略

**选择**：`_LLMWorker` 在 LLM 调用之前将上下文加载（记忆、知识、压缩）作为预处理步骤，而不是作为单独的图节点。

**理由**：对于聊天模式（单个 ConversationNode 图），上下文加载必须在节点内部发生。对于 workflow 模式，用户可以根据需要添加单独的 KnowledgeRetrieval 节点。两种模式都可以——Worker 的内部预处理对图拓扑是透明的。关键数据通过通道流动：
- 输入：`messages` 通道（用户消息作为 initial_input 注入）
- 输出：`messages` 通道（助手响应被追加）

**考虑的替代方案**：将上下文组装作为单独的图节点。被拒绝，因为这会强制聊天模式成为一个 5 节点图（context → memory → knowledge → LLM → suggestions），为最简单的用例增加了开销和复杂性。

### Decision 3：工具调用循环——循环图边，而非 Worker 内部循环

**选择**：工具调用在图中被建模为循环边模式：`ConversationNode → ConditionNode (has_tool_call?) → ToolNode → ConversationNode`。这替代了 ConversationService 的 `for iteration in range(max_iterations)` 循环。

**理由**：这正是 `build_three_layer_graph()` 已经工作的方式（planner → check_tools → tool_call → planner）。将其作为图模式意味着：
- PregelRuntime 的 `max_supersteps` 防止无限循环
- 检查点自动捕获循环中间状态
- 工具调用循环在图拓扑中可见（可观测、可调试）
- 中断/恢复在循环中间工作，无需特殊处理

**考虑的替代方案**：将工具循环保留在 `_LLMWorker` 内部（命令式）。被拒绝，因为它重复了 ConversationService 的方法并且失去了 Pregel 的优势（检查点、可观测性）。

### Decision 4：流式架构——Worker yield 令牌，PregelRuntime 透传

**选择**：`StreamMode.MESSAGES` 使 PregelRuntime 从 Workers yield 单个令牌。产生流式输出的 Workers（LLMWorker）yield `{"type": "message", "content": token}` 事件。PregelRuntime 收集并转发这些事件。

**理由**：目前 `StreamMode.UPDATES` 按节点 yield，`StreamMode.VALUES` yield 完整状态。`StreamMode.MESSAGES`（已在 types.py 中定义但未实现）填补了令牌级流式的空白。PregelRuntime 的超步循环已经 yield 事件——添加第三个 yield 路径是很自然的。

### Decision 5：聊天图模板结构

**选择**：`build_chat_graph()` 生成一个 3 节点图：
```
[__start__] → [conversation] → [check_tools] ──(有工具)──→ [tool_call] → [conversation] (循环)
                                   │
                                   (无工具)
                                   ▼
                              [suggestions] → [__end__]
```

当 `enable_suggestions=False` 或 `generate_opening=True` 时，通过条件边跳过建议节点。

**理由**：这反映了 ConversationService 的实际行为（LLM 调用 → 工具循环 → 建议）作为图。现有的 `build_three_layer_graph()` 已经使用了完全相同的模式（planner → check_tools → tool → loop）。

### Decision 6：建议和开场白作为后处理

**选择**：建议和开场白由 SUGGESTION NodeType 上的 `_SuggestionWorker` 处理，通过对话节点后的条件边触发。这替代了 ConversationService 的内联 `_generate_followup_suggestions()` 和 `_generate_opening_remarks()` 调用。

**理由**：建议是可选的、有条件的。将它们作为带有条件路由的单独节点比将其烘焙到 LLM Worker 中更干净。

### Decision 7：WorkflowExecutionService 作为统一入口点

**选择**：一个新的 `WorkflowExecutionService` 类接受 `AgentModel`，解析相应的图模板，编译它，创建正确的 Workers，注入 Guardrail Hooks，并运行 PregelRuntime。`chat.py` 和 workflow API 都调用这个服务。

**理由**：单一入口点意味着添加日志、可观测性、速率限制和错误处理只有一个地方。`chat.py` 成为一个薄 API 适配器。

### Decision 8：Guard 作为 Hook，而非图节点

**选择**：Guard 是一个通过 Guardrail Hooks（PreLLMHook、PostLLMHook、PreToolHook、PostToolHook）实现的横切关注点，由 WorkflowExecutionService 注入到 Workers 中。它不是一个图节点。`build_three_layer_graph()` 中的 guard 节点被移除。

**理由**：安全应该适用于所有执行模式（chat、three_layer、workflow），而不仅仅是 three_layer。现有的 guardrail.py 已经定义了完美的 Hook 接口：
- `PreLLMHook.on_pre_llm_call()` → 在 `_LLMWorker` 内部 LLM 调用之前调用
- `PostLLMHook.on_post_llm_call()` → 在 `_LLMWorker` 内部 LLM 响应之后调用
- `PreToolHook.on_pre_tool_call()` → 在 `_ToolWorker` 内部工具执行之前调用
- `PostToolHook.on_post_tool_call()` → 在 `_ToolWorker` 内部工具执行之后调用

Hook 优于 Node 的好处：
- 自动：无论图拓扑如何，每次 LLM 调用和工具调用都通过防护栏
- 无图污染：图模板专注于业务逻辑，而非安全基础设施
- 可插拔：每个 agent/workspace/tenant 使用不同的防护栏实现
- 目前 ABC + NoOp 默认值已存在——我们只需要将其接入 Workers

**考虑的替代方案**：在 three_layer 图中将 guard 保留为 CONVERSATION 节点。被拒绝，因为这意味着只有 three_layer 有安全，而且每个新的图模板都必须记得手动添加 guard 节点。

### Decision 9：通过 _AgentWorker 进行嵌套图执行

**选择**：`_AgentWorker` 通过 ID 解析子 agent，将父通道上下文（消息、变量）打包为 `initial_input`，并调用 `WorkflowExecutionService.execute()`——实现图内嵌图的执行。它不直接调用 `EnginePort.agent_execute()`。

**理由**：这反映了 Google ADK（LlmAgent 通过 WorkflowAgent 组合 sub_agents）和 IBM watsonx（Agent 将 Agentic Workflow 作为 Tool 调用）处理组合的方式。引擎层支持任意嵌套，因为 PregelRuntime 是可递归的——一个图中的 Worker 可以调用另一个图的执行。通过 WorkflowExecutionService 路由，子 agent 获得完整的模板解析、编译和 guard hook 注入管道，而不仅仅是一个原始的 LLM 调用。

这个决定为 P3 的 "Agent + Workflow 可组合性" 敞开了大门，而无需现在实现。当 P3 引入 Skill 概念（Agent 将 Workflow 作为 skill 挂载）时，引擎层不需要更改——只有 service/API 层需要一个根据 skills 映射到 Workers 或嵌套图执行的新 SkillRegistry。

**考虑的替代方案**：`_AgentWorker` 调用 `EnginePort.agent_execute()`，后者为聊天模式的子 agent 路由到旧的 ConversationService。被拒绝，因为这违背了统一执行的目的——聊天模式的子 agent 会完全绕过 PregelRuntime。

## Risks / Trade-offs — 风险/权衡

- **[流式回归]** → 当前的 SSE 流式通过 ConversationService 的生成器完美工作。迁移到 PregelRuntime 的 StreamMode.MESSAGES 需要仔细的实现和全面的回归测试。**缓解措施**：先实现 StreamMode.MESSAGES，独立测试，然后迁移 chat.py。

- **[简单聊天的 PregelRuntime 开销]** → 对于简单的"一问一答"，通过图编译、通道注册、Worker 调度、检查点增加了相对于直接 LLM 调用的开销。**缓解措施**：测量开销；如果超过 50ms，通过跳过非中断图的检查点来优化。

- **[工具调用循环正确性]** → 循环边模式（conversation → check_tools → tool_call → conversation）必须正确终止。PregelRuntime 的 `max_supersteps` 提供了保护，但 ConditionWorker 的表达式求值必须正确检测通道状态中的 tool_calls。**缓解措施**：针对 1、5 和 max_iterations 场景的工具调用循环进行全面测试。

- **[破坏性 API 变更面]** → 尽管外部 API（`POST /v1/chat/completions`）保持不变，但所有内部服务边界都发生变化。任何直接导入 ConversationService 的代码都会中断。**缓解措施**：在迁移前搜索所有 ConversationService 导入，更新所有调用点。

- **[图上下文中的证据跟踪]** → EvidenceTracker 目前从 ConversationService 接收 `session_id` 和 `turn_index`。在图上下文中，这些需要来自通道状态或 PregelRuntime 元数据。**缓解措施**：通过通道状态传递会话元数据，Workers 从通道读取。
