## MODIFIED Requirements — 修改的需求

### Requirement: Compiler validates entry point, edges, and handoff cycles — 编译器验证入口点、边和 Handoff 循环
`GraphCompiler.compile()` 应在生成 `CompiledGraph` 之前执行验证阶段：入口点、边、handoff 循环、fan-out/merge 结构约束、执行模式感知的节点限制、通道访问验证和路由配置验证。当 `execution_mode="task"` 传递给 compile() 时，编译器应通过抛出 `GraphValidationError` 来拒绝包含 INTERRUPT 或 SUGGESTION 节点类型的图谱。

#### Scenario: Entry point not found — 未找到入口点
- **当** 声明的入口点引用了一个不存在的节点
- **则** 应抛出 `GraphValidationError`，field="entry"

#### Scenario: Edge target references non-existent node — 边目标引用不存在的节点
- **当** 边目标既不是已声明的节点 ID 也不是哨兵（`__start__`、`__end__`）
- **则** 应抛出 `GraphValidationError`，字段指示边路径

#### Scenario: Unreachable nodes logged as warning — 不可达节点记录为警告
- **当** 存在无法通过 BFS 从入口点到达的节点
- **则** 编译器应记录一个包含不可达节点 ID 的 WARNING，但不应抛出错误

#### Scenario: Handoff between non-agent nodes — 非 Agent 节点间的 Handoff
- **当** handoff 边的源或目标不是 AGENT 类型的节点
- **则** 应抛出 `GraphValidationError`

#### Scenario: Fan-out without merge — Fan-out 无 Merge
- **当** 图谱包含 FAN_OUT 节点，但从其任何分支都无法到达 MERGE 节点
- **则** 应抛出 `GraphValidationError`，消息为 "FAN_OUT node '{id}' has no reachable MERGE node"

#### Scenario: Merge without fan-out — Merge 无 Fan-out
- **当** 图谱包含 MERGE 节点，但上游没有 FAN_OUT 节点
- **则** 应抛出 `GraphValidationError`，消息为 "MERGE node '{id}' has no upstream FAN_OUT node"

#### Scenario: Fan-out branches must match merge — Fan-out 分支必须匹配 Merge
- **当** FAN_OUT 节点有 3 个分支，但下游 MERGE 节点的配置列出了不同的 fan_out_source
- **则** 应抛出 `GraphValidationError`

#### Scenario: Task mode with INTERRUPT node — 任务模式包含 INTERRUPT 节点
- **当** `execution_mode="task"` 传递给 `compile()` 且图谱包含 INTERRUPT 节点
- **则** 编译器应抛出 `GraphValidationError`，消息指示 INTERRUPT 节点在任务模式下被禁止

#### Scenario: Task mode with SUGGESTION node — 任务模式包含 SUGGESTION 节点
- **当** `execution_mode="task"` 传递给 `compile()` 且图谱包含 SUGGESTION 节点
- **则** 编译器应抛出 `GraphValidationError`，消息指示 SUGGESTION 节点在任务模式下被禁止

#### Scenario: Conversational mode allows all node types — 对话模式允许所有节点类型
- **当** `execution_mode="conversational"` 传递给 `compile()` 且图谱包含 INTERRUPT 和 SUGGESTION 节点
- **则** 编译器应成功编译，不抛出与模式相关的错误

#### Scenario: No execution mode defaults to conversational — 未指定执行模式默认使用对话模式
- **当** 未向 `compile()` 提供 `execution_mode`
- **则** 编译器应默认采用 `"conversational"` 行为，允许所有节点类型

#### Scenario: Routing config validation for intent mode — 意图模式的路由配置验证
- **当** CONDITION 节点有 `routing_mode: "intent"` 但无 `routing_config.intent_patterns`
- **则** 编译器应抛出 `GraphValidationError`，指示意图路由需要 intent_patterns

#### Scenario: Routing config validation for dynamic mode — 动态模式的路由配置验证
- **当** CONDITION 节点有 `routing_mode: "dynamic"` 但无 `routing_config.candidate_agents`
- **则** 编译器应抛出 `GraphValidationError`，指示动态路由需要 candidate_agents

#### Scenario: Dynamic routing candidates must reference existing nodes — 动态路由候选必须引用现有节点
- **当** CONDITION 节点有 `routing_mode: "dynamic"` 和 `candidate_agents: ["agent_a", "nonexistent"]`
- **则** 编译器应抛出 `GraphValidationError`，指示候选 "nonexistent" 不是已声明的节点

#### Scenario: Invalid routing mode rejected — 无效的路由模式被拒绝
- **当** CONDITION 节点有 `routing_mode: "unknown"`
- **则** `parse_graph()` 应抛出 `GraphValidationError`

#### Scenario: Channel access warnings logged — 记录通道访问警告
- **当** 节点声明 `channels.readable: ["nonexistent"]` 且 "nonexistent" 不在图谱 `state` 中
- **则** 编译器应记录关于未声明通道访问的 WARNING

## MODIFIED Requirements — 修改的需求

### Requirement: Graph DSL parser validates against JSON Schema — Graph DSL 解析器根据 JSON Schema 验证
`parse_graph()` 函数应接受 JSON 字符串或字典，并根据 `schemas/graph-dsl.schema.json` 进行验证。模式应在通道定义中包含 `"persistent"` 作为可选的布尔属性。解析器应自动将已弃用的 `"persistent_topic"` 迁移到 `"topic"`（带 `persistent=True`）。模式还应支持 CONDITION 节点配置上的 `routing_mode` 和 `routing_config` 字段，以及 `"dynamic_handoff"` 作为有效的边触发器值。

#### Scenario: Persistent channel in JSON — JSON 中的持久化通道
- **当** `parse_graph()` 遇到通道定义 `"type": "topic", "persistent": true`
- **则** 应创建 `ChannelDef(type=ChannelType.TOPIC, persistent=True)`

#### Scenario: Deprecated persistent_topic — 已弃用的 persistent_topic
- **当** `parse_graph()` 遇到 `"type": "persistent_topic"`
- **则** 应创建 `ChannelDef(type=ChannelType.TOPIC, persistent=True)` 并记录弃用警告

#### Scenario: Custom registered type — 自定义注册类型
- **当** `parse_graph()` 遇到 `"type": "priority_queue"` 且 "priority_queue" 已在 ChannelTypeRegistry 中注册
- **则** 应创建 `ChannelDef(type=ChannelType("priority_queue"))`，无错误

#### Scenario: Unknown type — 未知类型
- **当** `parse_graph()` 遇到 `"type": "unknown"` 且 "unknown" 不在注册表中
- **则** 应抛出 `GraphValidationError`，字段指向通道类型

#### Scenario: Routing mode in DSL — DSL 中的路由模式
- **当** `parse_graph()` 遇到一个 CONDITION 节点，包含 `routing_mode: "intent"` 和 `routing_config`
- **则** 应无错误地将路由配置解析到 NodeConfig 中

#### Scenario: Dynamic handoff trigger in DSL — DSL 中的动态 Handoff 触发器
- **当** `parse_graph()` 遇到一条边，带有 `trigger: "dynamic_handoff"`
- **则** 结果 `Edge` 应设置 `trigger="dynamic_handoff"`
