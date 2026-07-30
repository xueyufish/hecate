## MODIFIED Requirements — 修改的需求

### 需求：EnginePort agent_execute 方法

系统应在 `EnginePort` 上添加一个 `agent_execute` 方法，该方法接受 agent_id、messages、channel_snapshot 和可选的 context，并返回包含 Agent 响应的 dict。具体实现应加载 Agent 配置的工具、查询知识库、应用守卫钩子（PreLLMHook/PostLLMHook），并在调用 LLM 之前调用 context_assemble — 匹配 LLMWorker 用于 CONVERSATION 节点的完整流水线。

#### 场景：通过端口的 Agent 执行
- **当** 调用 `agent_execute(agent_id=UUID("..."), messages=[{"role": "user", "content": "hello"}], channel_snapshot={})` 时
- **则** 端口按 ID 解析 AgentModel，加载 Agent 配置的工具，查询 Agent 的知识库，应用 PreLLMHook，调用 context_assemble，使用工具调用 LLM，应用 PostLLMHook，并返回 `{"response": "...", "usage": {...}}`

#### 场景：找不到 Agent
- **当** 调用 `agent_execute(agent_id=UUID("nonexistent"), ...)` 时
- **则** 端口抛出 `ValueError`，消息指示找不到 Agent

#### 场景：带工具过滤的 Agent 执行
- **当** 使用指定 `tools: ["web_search", "lookup_invoice"]` 的 `agent_definition` 调用 `agent_execute` 时
- **则** 只有 AgentDefinition 中列出的工具传递给 LLM，而非 Agent 的完整工具列表

#### 场景：带知识库的 Agent 执行
- **当** Agent 在其 AgentModel 中配置了知识库时
- **则** 系统使用用户消息作为上下文查询这些知识库，并将相关片段注入到 LLM 上下文中

#### 场景：PreLLMHook 阻止 Agent 执行
- **当** PreLLMHook 对 Agent 的消息返回 action=BLOCK 时
- **则** 系统返回一个指示请求被阻止的响应，而不调用 LLM

### 需求：AGENT 节点实际执行

系统应根据节点配置中的 `invocation_mode` 字段执行 AGENT 类型节点。当 `invocation_mode` 为 `"graph"`（默认）时，Worker 委托给 WorkflowExecutionService 进行嵌套图执行。当 `invocation_mode` 为 `"tool"` 时，目标 Agent 通过 AgentDefinition 注册为可调用的工具。

#### 场景：使用图调用模式的 AGENT 节点
- **当** 图使用配置 `{"agent_id": "uuid-of-agent", "invocation_mode": "graph"}` 执行 AGENT 节点时
- **则** Worker 委托给 WorkflowExecutionService 进行嵌套图执行（现有行为）

#### 场景：使用工具调用模式的 AGENT 节点
- **当** 图使用配置 `{"agent_id": "uuid-of-specialist", "invocation_mode": "tool", "agent_definition": {"tools": ["web_search"], "context_mode": "isolated"}}` 执行 AGENT 节点时
- **则** Worker 使用 AgentDefinition 创建 AgentTool 并将其注册到父 Agent 的工具列表中

#### 场景：缺少 agent_id 的 AGENT 节点
- **当** 图使用配置 `{}`（无 agent_id）执行 AGENT 节点时
- **则** Worker 返回包含指示缺少 agent_id 错误的 `WorkerResult`

#### 场景：使用默认调用模式的 AGENT 节点
- **当** 图使用配置 `{"agent_id": "uuid-of-agent"}`（无 invocation_mode 字段）执行 AGENT 节点时
- **则** Worker 将 invocation_mode 视为 `"graph"` 并委托给 WorkflowExecutionService（向后兼容）

### 需求：Agent-as-Tool 动态注册

系统应在 AGENT 节点配置中支持 `invocation_mode` 字段。当 `invocation_mode: "tool"` 时，目标 Agent 应在执行期间被注册为父 Agent 工具列表中的可调用工具，使用 `AgentDefinition` 进行权限范围限定。

#### 场景：使用 AgentDefinition 将 Agent 暴露为工具
- **当** AGENT 节点具有配置 `{"agent_id": "uuid-of-specialist", "invocation_mode": "tool", "agent_definition": {"tools": ["web_search"], "context_mode": "isolated"}}` 时
- **则** 父 Agent 的工具列表包含名为 `agent_{specialist_name}` 的工具，应用了 AgentDefinition 的工具过滤和上下文隔离

#### 场景：不带 AgentDefinition 将 Agent 暴露为工具（现有行为）
- **当** AGENT 节点具有配置 `{"agent_id": "uuid-of-specialist", "invocation_mode": "tool"}`（无 agent_definition）时
- **则** 父 Agent 的工具列表包含名为 `agent_{specialist_name}` 的工具，具有完整的工具继承（现有行为不变）

#### 场景：使用过滤工具的 Agent 工具调用
- **当** 父 LLM 使用参数 `{"task": "analyze this data"}` 调用 `agent_{specialist_name}` 工具时
- **则** 系统仅使用 AgentDefinition 中指定的工具执行专家 Agent，而非专家的完整工具列表

### 需求：每 Agent 的上下文隔离

系统应为每个 Agent 调用提供隔离的执行上下文。每个 Agent 应使用其 AgentModel 中定义的自有系统提示（来自 persona 字段）、工具和知识库。

#### 场景：专家 Agent 使用自己的系统提示
- **当** 角色为 "You are a billing expert" 的 Agent "billing_specialist" 从 Agent "triage" 被调用时
- **则** billing_specialist 的 LLM 调用使用 "You are a billing expert" 作为系统提示，而非 triage Agent 的系统提示

#### 场景：专家 Agent 使用自己的工具
- **当** Agent "billing_specialist" 在其 AgentModel 中具有工具 `["lookup_invoice", "process_refund"]` 时
- **则** 在 billing_specialist 的执行期间只有这些工具可用，而非父 Agent 的工具

#### 场景：专家 Agent 使用自己的知识库
- **当** Agent "billing_specialist" 在其 AgentModel 中具有知识库 `["billing_docs_kb"]` 时
- **则** billing_specialist 的执行查询这些知识库，而非父 Agent 的知识库
