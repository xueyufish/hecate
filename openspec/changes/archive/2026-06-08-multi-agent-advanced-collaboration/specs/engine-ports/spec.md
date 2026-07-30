## 修改的需求

### 需求：用于多智能体的可选 agent_execute 方法
`agent_execute()` 方法应接受一个可选的 `agent_definition: AgentDefinition | None = None` 参数。当提供此参数时，执行应使用 AgentDefinition 的覆盖配置（工具过滤、上下文模式、模型覆盖、max_turns）而非智能体的默认配置。

#### 场景：不带定义的智能体执行（现有行为）
- **当** 调用 `agent_execute(agent_id=UUID("..."), messages=[...], channel_snapshot={})` 而不带 agent_definition
- **则** 执行应使用智能体配置的工具、提示词、模型和上下文进行（现有行为不变）

#### 场景：带定义覆盖的智能体执行
- **当** 调用 `agent_execute(agent_id=UUID("..."), messages=[...], channel_snapshot={}, agent_definition=AgentDefinition(agent_id=UUID("..."), tools=["web_search"], context_mode="isolated"))`
- **则** 执行应仅使用 `["web_search"]` 作为工具列表，创建隔离的消息上下文，并使用智能体配置的模型

#### 场景：未实现的智能体执行
- **当** 具体的 EnginePort 未覆盖 `agent_execute()`
- **则** 调用它应抛出 NotImplementedError，附带消息 "agent_execute requires a concrete EnginePort adapter"
