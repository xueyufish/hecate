## 新增需求

### 需求：EnginePort agent_execute 方法
系统须向 `EnginePort` 添加 `agent_execute` 方法，接受 agent_id、messages、channel_snapshot 和可选的 context，返回包含 agent 响应的 dict。

#### 场景：通过端口执行 Agent
- **当** 调用 `agent_execute(agent_id=UUID("..."), messages=[{"role": "user", "content": "hello"}], channel_snapshot={})`
- **则** 端口按 ID 解析 AgentModel，从 agent 的 persona/tools/knowledge bases 构建隔离上下文，调用 LLM，返回 `{"response": "...", "usage": {...}}`

#### 场景：Agent 未找到
- **当** 调用 `agent_execute(agent_id=UUID("nonexistent"), ...)`
- **则** 端口抛出 `ValueError`，消息指示 agent 未找到

### 需求：AGENT 节点真实执行
系统须通过调用 `EnginePort.agent_execute()` 执行 AGENT 类型节点，agent_id 来自节点配置。Agent 的响应须写入 `messages` channel。

#### 场景：AGENT 节点带有有效 agent_id
- **当** 图执行一个 AGENT 节点，配置为 `{"agent_id": "uuid-of-agent", "channels": {"readable": ["messages"], "writable": ["messages"]}}`
- **则** worker 调用 `port.agent_execute(agent_id, messages_from_channel, channel_snapshot)` 并将响应写入 `messages` channel

#### 场景：AGENT 节点缺少 agent_id
- **当** 图执行一个 AGENT 节点，配置为 `{}`（无 agent_id）
- **则** worker 返回 `WorkerResult`，附带指示缺少 agent_id 的错误

### 需求：Agent-as-Tool 动态注册
系统须支持 AGENT 节点配置中的 `invocation_mode` 字段。当 `invocation_mode: "tool"` 时，目标 agent 须在执行期间注册为父 agent 工具列表中的可调用工具。

#### 场景：Agent 暴露为工具
- **当** AGENT 节点配置为 `{"agent_id": "uuid-of-specialist", "invocation_mode": "tool"}`
- **则** 父 agent 的工具列表包含名为 `agent_{specialist_name}` 的工具，描述来自 specialist 的 persona

#### 场景：Agent 工具调用
- **当** 父 LLM 调用 `agent_{specialist_name}` 工具，参数为 `{"task": "analyze this data"}`
- **则** 系统以任务作为输入执行 specialist agent，并将 specialist 的响应作为工具结果返回

### 需求：每个 Agent 的上下文隔离
系统须为每个 agent 调用提供隔离的执行上下文。每个 Agent 须使用其自己的 system prompt（来自 persona 字段）、工具和知识库（如其 AgentModel 中所定义）。

#### 场景：专业 Agent 使用自己的 system prompt
- **当** agent "billing_specialist"（persona 为 "You are a billing expert"）从 agent "triage" 被调用
- **则** billing_specialist 的 LLM 调用使用 "You are a billing expert" 作为 system prompt，而非 triage agent 的 system prompt

#### 场景：专业 Agent 使用自己的工具
- **当** agent "billing_specialist" 在其 AgentModel 中有工具 `["lookup_invoice", "process_refund"]`
- **则** 在 billing_specialist 执行期间仅这些工具可用，而非父 agent 的工具
