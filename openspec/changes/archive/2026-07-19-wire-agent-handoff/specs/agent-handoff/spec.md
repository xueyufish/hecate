## ADDED Requirements — 新增需求

### 需求：在 Agent 执行时注入 Handoff 工具

当 AGENT 节点具有 `trigger` 为 `"handoff"` 或 `"dynamic_handoff"` 的出站边时，`AgentExecutionPort.agent_execute()` 应在调用 LLM 之前将 `handoff_to_agent` 工具注入到 LLM 的工具列表中。该工具应接受一个 `target` 参数，其 `enum` 是通过这些边可达的有效目标节点 ID 列表。如果不存在此类边，则不应注入该工具。

#### 场景：具有单个静态目标的 Handoff 工具注入
- **当** AGENT 节点 "router" 有一条出站边 `{"source": "router", "target": "billing", "trigger": "handoff"}` 时
- **且** 为 "router" 调用 `agent_execute`
- **则** LLM 收到名为 `handoff_to_agent` 的工具，其 `parameters.properties.target.enum` 等于 `["billing"]`

#### 场景：具有多个动态目标的 Handoff 工具注入
- **当** AGENT 节点 "triage" 有一条出站边 `{"source": "triage", "target": {"billing": "billing_agent", "tech": "tech_agent"}, "trigger": "dynamic_handoff"}` 时
- **且** 为 "triage" 调用 `agent_execute`
- **则** LLM 收到 `handoff_to_agent` 工具，其 `target.enum` 等于 `["billing_agent", "tech_agent"]`（字典值，去重）

#### 场景：无 Handoff 边则不产生 Handoff 工具
- **当** AGENT 节点没有出站 `handoff` 或 `dynamic_handoff` 边时
- **则** `agent_execute` 不应注入 `handoff_to_agent` 工具

#### 场景：Handoff 目标来自 execution_context
- **当** `PregelRuntime._dispatch_node` 为具有出站 handoff 边的节点调用 Worker 时
- **则** Worker 的 `execution_context` 应包含一个 `handoff_targets` 键，持有 `{"node_id": str, "description": str}` 对象列表，每个可达目标一个

### 需求：Handoff 工具调用检测和 Command 生成

当 LLM 的响应包含对 `handoff_to_agent` 的调用且包含有效的 `target` 时，`AgentExecutionPort.agent_execute()` 应返回包含 `handoff_to: <target_node_id>` 的结果字典。`AgentWorker` 应将其转换为 `command` 为 `Command(goto=<target_node_id>)` 的 `WorkerResult`。

#### 场景：有效的 Handoff 目标产生 Command(goto=...)
- **当** LLM 调用 `handoff_to_agent(target="tech_agent")` 且 `"tech_agent"` 在有效目标列表中时
- **则** `agent_execute` 返回 `{"response": "", "handoff_to": "tech_agent"}`
- **且** `AgentWorker.execute` 返回 `WorkerResult(command=Command(goto="tech_agent"))`

#### 场景：无效的 Handoff 目标被拒绝
- **当** LLM 调用 `handoff_to_agent(target="unknown")` 且 `"unknown"` 不在有效目标列表中时
- **则** 端口应向 LLM 返回错误响应指示目标无效，提示重试
- **且** 不应产生 `Command(goto=...)`

#### 场景：PregelRuntime 支持 Command
- **当** `WorkerResult(command=Command(goto="tech_agent"))` 返回给 `PregelRuntime` 时
- **则** 下一个超步骤应执行 "tech_agent" 节点

### 需求：每个目标的 Handoff 工具描述

`handoff_to_agent` 工具的描述应包含每个候选目标的角色，以便 LLM 可以准确路由。每个目标的描述应按优先级顺序来源于：(1) 源 AGENT 节点的 `handoff.description` 覆盖；(2) 目标 Agent 的 `AgentModel.description`；(3) 目标 AGENT 节点的 `name`。

#### 场景：描述首先使用节点级别覆盖
- **当** 源 AGENT 节点配置包含 `{"handoff": {"description": "Specialist agents for the support desk"}}` 时
- **则** `handoff_to_agent` 工具描述应包含 "Specialist agents for the support desk"

#### 场景：描述回退到目标 AgentModel 描述
- **当** 源节点上没有 `handoff.description` 覆盖时
- **且** 目标 Agent 的 `AgentModel.description` 是 "Handles billing inquiries"
- **则** 该目标的工具描述应包含 "Handles billing inquiries"

#### 场景：没有其他可用信息时描述回退到节点名称
- **当** 目标的 `handoff.description` 和 `AgentModel.description` 都未设置时
- **则** 工具描述应使用目标 AGENT 节点的 `name`

### 需求：Handoff 的上下文模式

系统应支持三种上下文传递策略，用于 handoff 后的下游 Agent，由源 AGENT 节点配置上的可选 `handoff.context_mode` 字段控制。有效值为 `"inherited"`（默认）、`"isolated"` 和 `"summarized"`。

#### 场景：Inherited 模式传递完整消息历史
- **当** AGENT 节点具有 `handoff.context_mode == "inherited"`（或字段不存在）时
- **且** 发生到目标 "tech_agent" 的 handoff
- **则** handoff 的 `messages` 通道写入应包括在 handoff 时刻完整的消息历史，后跟 "AIMessage + ToolMessage 配对" 需求中描述的 AIMessage + ToolMessage 对

#### 场景：Isolated 模式新建开始
- **当** AGENT 节点具有 `handoff.context_mode == "isolated"` 时
- **且** 发生 handoff
- **则** `messages` 通道写入应仅包含：(a) AIMessage + ToolMessage 对，以及 (b) 单个系统通知 `"Handed off from {source_node_id}"` — 没有先前的历史

#### 场景：Summarized 模式折叠历史
- **当** AGENT 节点具有 `handoff.context_mode == "summarized"` 时
- **且** 发生 handoff
- **则** `messages` 通道写入应包含：(a) 单个 `system` 消息，带有结构化摘要（`from`、`intent`、`key_facts`、`open_questions`），以及 (b) AIMessage + ToolMessage 对

#### 场景：无效的 context_mode 被编译器拒绝
- **当** 编译器遇到 `handoff.context_mode` 设置为非 `"inherited"`、`"isolated"` 或 `"summarized"` 值的 AGENT 节点时
- **则** 编译应失败，抛出 `GraphValidationError` 描述无效值

#### 场景：默认 context_mode 是 inherited
- **当** AGENT 节点没有 `handoff.context_mode` 字段时
- **则** 运行时应将模式视为 `"inherited"`

### 需求：Handoff 上的 AIMessage + ToolMessage 配对

当发生 handoff 时，`messages` 通道更新应恰好包含一个 `AIMessage`（LLM 的工具调用消息，保留原始 `tool_call_id`）与恰好一个 `ToolMessage` 配对，其 `tool_call_id` 与 AIMessage 匹配，内容为 `"Handed off to {target_node_id}"`。不应写入未配对的工具调用消息。

#### 场景：配对产生有效的对话历史
- **当** LLM 使用 `tool_call_id="call_abc123"` 调用 `handoff_to_agent` 时
- **则** 生成的 `messages` 通道更新应包含带有 `tool_calls=[{"id": "call_abc123", ...}]` 的 `AIMessage` 和带有 `tool_call_id="call_abc123"` 的 `ToolMessage`

#### 场景：工具调用 ID 精确保留
- **当** LLM 提供者返回 `tool_call_id` 为 `"call_xyz"` 时
- **则** 相同的 `"call_xyz"` 应出现在通道更新中的 AIMessage 和 ToolMessage 上

#### 场景：重复 tool_call_id 上的冲突生成 UUID 后缀
- **当** LLM 提供者为两次连续的 handoff 调用返回相同的 `tool_call_id`（罕见的边界情况）时
- **则** 第二次出现应在 AIMessage 和 ToolMessage 上都重命名为 `"{original_id}-{uuid4_hex[:8]}"`
- **且** 应记录 WARNING

### 需求：PregelRuntime 在 execution_context 中填充 handoff_targets

在派遣 Worker 处理 AGENT 节点之前，`PregelRuntime` 应检查编译图的出站边，并用 `{"node_id": str, "description": str}` 对象列表填充 `execution_context["handoff_targets"]`，每个 `trigger` 为 `"handoff"` 或 `"dynamic_handoff"` 的边一个对象。当不存在此类边时，列表应为空（或键不存在）。

#### 场景：单个静态 handoff 边填充一个目标
- **当** PregelRuntime 为节点 "router" 派遣 Worker，该节点有一条出站边 `{"source": "router", "target": "billing", "trigger": "handoff"}` 时
- **则** `execution_context["handoff_targets"]` 应等于 `[{"node_id": "billing", "description": <解析的描述>}]`

#### 场景：动态 handoff 边填充所有字典值
- **当** PregelRuntime 为节点 "triage" 派遣 Worker，该节点有一条出站边 `{"source": "triage", "target": {"billing": "billing_agent", "tech": "tech_agent"}, "trigger": "dynamic_handoff"}` 时
- **则** `execution_context["handoff_targets"]` 应包含两个条目：`{"node_id": "billing_agent", ...}` 和 `{"node_id": "tech_agent", ...}`

#### 场景：无 Handoff 边则返回空列表
- **当** PregelRuntime 为没有出站 handoff 或 dynamic_handoff 边的节点派遣 Worker 时
- **则** `execution_context["handoff_targets"]` 应为空列表（或键不存在）

#### 场景：非 AGENT 节点不接收 handoff_targets
- **当** PregelRuntime 为 CONVERSATION、CONDITION 或其他非 AGENT 节点类型派遣 Worker 时
- **则** `execution_context["handoff_targets"]` 不应被填充，无论出站边如何
- **注意** 此限制将本次变更限定在 AGENT 节点；CONVERSATION handoff 是单独的将来变更。
