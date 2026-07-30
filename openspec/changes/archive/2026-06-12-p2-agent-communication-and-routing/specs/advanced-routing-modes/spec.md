## ADDED Requirements — 新增需求

### Requirement: Routing mode field on CONDITION node config — CONDITION 节点配置上的路由模式字段
CONDITION 节点配置应支持一个可选的 `routing_mode` 字段，值为 `"condition"`（默认）、`"intent"` 和 `"dynamic"`。当 `routing_mode` 不存在或为 `"condition"` 时，行为与现有的基于表达式的路由相同。

#### Scenario: Default routing mode is condition — 默认路由模式为 condition
- **当** CONDITION 节点没有 `routing_mode` 字段
- **则** 节点应使用基于表达式的路由，与当前行为相同

#### Scenario: Explicit condition mode — 显式 condition 模式
- **当** CONDITION 节点有 `routing_mode: "condition"`
- **则** 节点应评估 `expression` 字段，并将结果写入 `_route` 通道

#### Scenario: Invalid routing mode rejected — 无效的路由模式被拒绝
- **当** CONDITION 节点有 `routing_mode: "unknown"`
- **则** `parse_graph()` 应抛出 `GraphValidationError`，字段指示无效的路由模式

### Requirement: Intent-based routing mode — 基于意图的路由模式
当 `routing_mode: "intent"` 时，CONDITION 节点应基于意图分类进行路由。`routing_config` 字段应包含 `intent_patterns`（`{pattern: str, target: str}` 对象的列表）和一个可选的 `routing_prompt`（字符串）。引擎应首先尝试对输入通道值进行正则模式匹配。如果没有模式匹配且提供了 `routing_prompt`，引擎应调用 `EnginePort.llm_invoke()` 来分类意图。`_route` 值应设置为匹配的目标。

#### Scenario: Intent pattern match — 意图模式匹配
- **当** CONDITION 节点有 `routing_mode: "intent"` 和 `intent_patterns: [{pattern: "billing|invoice", target: "billing_agent"}, {pattern: "technical|bug", target: "tech_support"}]`
- **并且** 输入通道值包含 "I have a billing question"
- **则** `_route` 值应为 "billing_agent"

#### Scenario: Intent pattern no match with LLM fallback — 意图模式无匹配使用 LLM 后备
- **当** CONDITION 节点有 `routing_mode: "intent"`、`intent_patterns: [{pattern: "billing", target: "billing_agent"}]` 和 `routing_prompt: "Classify the user intent into one of: billing, technical, general"`
- **并且** 输入通道值包含 "How do I reset my password?"
- **并且** 无模式匹配
- **则** 引擎应使用路由提示和输入调用 `EnginePort.llm_invoke()`
- **并且** LLM 响应应用于确定 `_route` 值

#### Scenario: Intent pattern no match without LLM fallback — 意图模式无匹配且无 LLM 后备
- **当** CONDITION 节点有 `routing_mode: "intent"` 和 `intent_patterns: [{pattern: "billing", target: "billing_agent"}]`
- **并且** 输入通道值包含 "Hello, how are you?"
- **并且** 未提供 `routing_prompt`
- **则** `_route` 值应设置为边目标字典中的 "default" 键

#### Scenario: Intent routing config validation — 意图路由配置验证
- **当** CONDITION 节点有 `routing_mode: "intent"` 但无 `routing_config.intent_patterns`
- **则** 编译器应抛出 `GraphValidationError`，消息指示意图路由需要 intent_patterns

### Requirement: Dynamic routing mode — 动态路由模式
当 `routing_mode: "dynamic"` 时，CONDITION 节点应调用 `EnginePort.llm_invoke()` 从 `candidate_agents` 列表中选择下一个发言者。`routing_config` 应包含 `candidate_agents`（节点 ID 列表）、`routing_prompt`（字符串）和一个可选的 `allow_repeated_speaker`（布尔值，默认 false）。LLM 响应应针对 `candidate_agents` 列表进行验证。

#### Scenario: Dynamic routing selects valid agent — 动态路由选择有效 agent
- **当** CONDITION 节点有 `routing_mode: "dynamic"`、`candidate_agents: ["agent_a", "agent_b", "agent_c"]` 和 `routing_prompt: "Select the best agent to respond"`
- **则** 引擎应使用路由提示、可用通道状态和候选列表调用 `EnginePort.llm_invoke()`
- **并且** 如果 LLM 返回 "agent_b"，则 `_route` 值应为 "agent_b"

#### Scenario: Dynamic routing invalid response falls back to default — 动态路由无效响应回退到默认
- **当** CONDITION 节点有 `routing_mode: "dynamic"`、`candidate_agents: ["agent_a", "agent_b"]`
- **并且** LLM 返回不在候选列表中的 "unknown_agent"
- **则** `_route` 值应设置为边目标字典中的 "default" 键

#### Scenario: Dynamic routing allow_repeated_speaker false — 动态路由 allow_repeated_speaker 为 false
- **当** CONDITION 节点有 `routing_mode: "dynamic"`、`allow_repeated_speaker: false`，且上一个发言者为 "agent_a"
- **则** "agent_a" 应从发送给 LLM 的候选列表中排除

#### Scenario: Dynamic routing config validation — 动态路由配置验证
- **当** CONDITION 节点有 `routing_mode: "dynamic"` 但无 `routing_config.candidate_agents`
- **则** 编译器应抛出 `GraphValidationError`，消息指示动态路由需要 candidate_agents

#### Scenario: Dynamic routing candidate must reference existing agent nodes — 动态路由候选必须引用现有 agent 节点
- **当** CONDITION 节点有 `routing_mode: "dynamic"` 和 `candidate_agents: ["agent_a", "nonexistent"]`
- **则** 编译器应抛出 `GraphValidationError`，指示候选 "nonexistent" 不是已声明的节点

### Requirement: Routing config schema in Graph DSL — Graph DSL 中的路由配置模式
CONDITION 节点配置中的 `routing_config` 对象应符合基于 `routing_mode` 的区分模式。Graph DSL JSON Schema 应更新以包含 `routing_mode`、具有 `intent_patterns`、`candidate_agents`、`routing_prompt` 和 `allow_repeated_speaker` 字段的 `routing_config`。

#### Scenario: Intent routing config in DSL — DSL 中的意图路由配置
- **当** `parse_graph()` 收到一个 CONDITION 节点，包含 `routing_mode: "intent"` 和 `routing_config: {intent_patterns: [{pattern: "sales", target: "sales_agent"}], routing_prompt: "Classify intent"}`
- **则** 解析后的 `NodeConfig` 应包含 `routing_mode="intent"` 和 routing_config 值

#### Scenario: Dynamic routing config in DSL — DSL 中的动态路由配置
- **当** `parse_graph()` 收到一个 CONDITION 节点，包含 `routing_mode: "dynamic"` 和 `routing_config: {candidate_agents: ["a", "b"], routing_prompt: "Pick best agent", allow_repeated_speaker: true}`
- **则** 解析后的 `NodeConfig` 应包含 `routing_mode="dynamic"` 和 routing_config 值
