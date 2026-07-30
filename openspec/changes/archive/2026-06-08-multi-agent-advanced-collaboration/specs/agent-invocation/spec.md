## 修改的需求

### 需求：Agent-as-Tool 动态注册
系统应在 AGENT 节点配置中支持 `invocation_mode` 字段。当设置 `invocation_mode: "tool"` 时，目标智能体应在父智能体的执行工具列表中注册为可调用工具，并在提供时使用 `AgentDefinition` 进行权限范围限定。

#### 场景：通过 AgentDefinition 将智能体暴露为工具
- **当** 一个 AGENT 节点配置为 `{"agent_id": "uuid-of-specialist", "invocation_mode": "tool", "agent_definition": {"tools": ["web_search"], "context_mode": "isolated"}}`
- **则** 父智能体的工具列表包含名为 `agent_{specialist_name}` 的工具，并应用了 AgentDefinition 的工具过滤和上下文隔离

#### 场景：不带 AgentDefinition 将智能体暴露为工具（现有行为）
- **当** 一个 AGENT 节点配置为 `{"agent_id": "uuid-of-specialist", "invocation_mode": "tool"}`（无 agent_definition）
- **则** 父智能体的工具列表包含名为 `agent_{specialist_name}` 的工具，并继承完整工具集（现有行为不变）

#### 场景：使用过滤工具调用智能体工具
- **当** 父 LLM 使用参数 `{"task": "analyze this data"}` 调用 `agent_{specialist_name}` 工具
- **则** 系统应仅使用 AgentDefinition 中指定的工具执行专家智能体，而非该专家的完整工具列表
