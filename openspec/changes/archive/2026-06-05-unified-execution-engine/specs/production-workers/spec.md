## ADDED Requirements — 新增需求

### Requirement: LLM Worker 处理完整的会话预处理 — LLM Worker handles full conversation pre-processing
`_LLMWorker` 应是用于 CONVERSATION 节点的生产级 Worker，按顺序执行上下文组装、记忆加载、压缩、知识检索、provider 塑造和 LLM 调用。它应通过 EnginePort 调用现有服务（ContextAssembler、WorkingMemoryService、UserMemoryService、CompressionPipeline、knowledge_base_service、llm_service）。

#### Scenario: 无工具的简单聊天 — Simple chat without tools
- **当** 一个 CONVERSATION 节点以 `{"model": "gpt-4o", "system_prompt": "You are helpful"}` 配置执行且 `messages` 通道包含用户消息
- **则** Worker 应组装上下文、调用 LLM，并在 channel_updates 中返回 `{"messages": [{"role": "assistant", "content": "response"}]}`

#### Scenario: 返回工具调用的聊天 — Chat with tool calls returned
- **当** LLM 响应包含 tool_calls
- **则** Worker 应在 channel_updates 中返回 `{"messages": [assistant_message_with_tool_calls], "_has_tool_call": true}`
- **并且** `_has_tool_call` 值应由下游的 ConditionNode 用于路由到 ToolWorker

#### Scenario: 流式令牌输出 — Streaming token output
- **当** StreamMode 为 MESSAGES
- **则** Worker 应在返回最终 WorkerResult 之前，从 llm_service.chat_stream() yield 单个令牌

#### Scenario: 带记忆的上下文组装 — Context assembly with memory
- **当** node config 中提供了 agent_id 和 user_id
- **则** Worker 应在 LLM 调用前加载 L1 记忆块（WorkingMemoryService）、L3 用户记忆（UserMemoryService）、压缩历史（CompressionPipeline）并组装上下文（ContextAssembler）

#### Scenario: 知识检索集成 — Knowledge retrieval integration
- **当** node config 中提供了 kb_ids
- **则** Worker 应对每个 KB 调用 knowledge_base_service.search() 并将结果注入上下文组装

### Requirement: Tool Worker 执行工具并记录证据 — Tool Worker executes tools with evidence tracking
`_ToolWorker` 应从通道状态解析工具调用，执行它们，捕获证据，并将结果注入回消息中。

#### Scenario: 执行工具调用 — Execute tool call
- **当** 一个 TOOL_CALL 节点执行且 `messages` 通道包含带有 tool_calls 的 assistant 消息
- **则** Worker 应解析工具调用，执行每个工具，并在 channel_updates 中返回 `{"messages": [tool_result_messages]}`

#### Scenario: 工具执行错误 — Tool execution error
- **当** 工具执行抛出异常
- **则** Worker 应将错误作为带有 `is_error: true` 的工具结果消息返回，而非抛出异常

#### Scenario: 证据捕获 — Evidence capture
- **当** 通过 EnginePort 提供了 EvidenceTracker
- **则** Worker 应为每个工具执行捕获工具名称、参数、结果、session_id 和 turn_index

### Requirement: Knowledge Worker 检索文档 — Knowledge Worker retrieves documents
`_KnowledgeWorker` 应查询知识库并将检索到的块写入通道。

#### Scenario: 知识检索 — Knowledge retrieval
- **当** 一个 KNOWLEDGE_RETRIEVAL 节点以 `{"kb_ids": ["uuid1"], "query_template": "{messages[-1].content}", "top_k": 5}` 执行
- **则** Worker 应从消息中提取查询，搜索指定的 KB，并在 channel_updates 中返回 `{"context": "retrieved content", "messages": [{"role": "system", "content": "Retrieved N docs"}]}`

### Requirement: Condition Worker 评估表达式 — Condition Worker evaluates expressions
`_ConditionWorker` 应针对通道状态评估表达式并将结果写入 `_route` 通道。

#### Scenario: 工具调用检测 — Tool call detection
- **当** 一个带有表达式 `has_tool_call` 的 CONDITION 节点在 `_has_tool_call` 为 `true` 的通道状态下评估
- **则** Worker 应在 channel_updates 中返回 `{"_route": "true"}`

#### Scenario: 表达式评估 — Expression evaluation
- **当** 一个带有表达式 `category == 'finance'` 的 CONDITION 节点在 `category` 为 `"finance"` 的通道状态下评估
- **则** Worker 应在 channel_updates 中返回 `{"_route": "true"}`

### Requirement: Agent Worker 委派给子 agent — Agent Worker delegates to sub-agent
`_AgentWorker` 应使用 node config 中的 agent_id 调用 EnginePort.agent_execute()。

#### Scenario: 子 agent 调用 — Sub-agent invocation
- **当** 一个 AGENT 节点以 `{"agent_id": "uuid", "invocation_mode": "direct"}` 执行
- **则** Worker 应调用 `EnginePort.agent_execute(agent_id, messages)` 并在 channel_updates 中返回子 agent 的响应

### Requirement: Suggestion Worker 生成跟进问题 — Suggestion Worker generates follow-up questions
`_SuggestionWorker` 应调用 SuggestionService 根据节点配置生成开场白或跟进建议。

#### Scenario: 跟进建议 — Follow-up suggestions
- **当** SUGGESTION 节点在一次会话轮次后以 `{"agent_persona": "...", "enable_suggestions": true}` 执行
- **则** Worker 应在 channel_updates 中返回 `{"suggested_questions": ["q1", "q2", "q3"]}`

#### Scenario: 开场白 — Opening remarks
- **当** SUGGESTION 节点以 `{"generate_opening": true}` 执行且这是第一轮
- **则** Worker 应在 channel_updates 中返回 `{"content": "opening text", "suggested_questions": ["q1", "q2"]}`

### Requirement: Variable Set Worker 将值写入通道 — Variable Set Worker writes values to channels
`_VariableSetWorker` 应将配置的变量值写入通道。

#### Scenario: 变量赋值 — Variable assignment
- **当** 一个 VARIABLE_SET 节点以 `{"variable_name": "status", "value": "completed"}` 执行
- **则** Worker 应在 channel_updates 中返回 `{"status": "completed"}`
