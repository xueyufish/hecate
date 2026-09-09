## MODIFIED Requirements

### Requirement: Compiler validates entry point, edges, and handoff cycles
The `GraphCompiler.compile()` SHALL perform validation stages before producing a `CompiledGraph`: entry point, edges, handoff cycles, fan-out/merge structural constraints, execution-mode-aware node restrictions, channel access validation, routing config validation, and declarative interrupt list validation（列表引用的 node ID 必须存在）。When `execution_mode="task"` is passed to compile(), the compiler SHALL reject graphs containing SUGGESTION node types or non-empty `interrupt_before`/`interrupt_after` lists by raising `GraphValidationError`.

#### Scenario: Entry point not found
- **WHEN** the declared entry point references a non-existent node
- **THEN** it SHALL raise `GraphValidationError` with field="entry"

#### Scenario: Edge target references non-existent node
- **WHEN** an edge target is neither a declared node ID nor a sentinel (`__start__`, `__end__`)
- **THEN** it SHALL raise `GraphValidationError` with field indicating the edge path

#### Scenario: Unreachable nodes logged as warning
- **WHEN** nodes exist that are not reachable from the entry point via BFS
- **THEN** the compiler SHALL log a WARNING with the unreachable node IDs but SHALL NOT raise an error

#### Scenario: Handoff between non-agent nodes
- **WHEN** a handoff edge source or target is not an AGENT-type node
- **THEN** it SHALL raise `GraphValidationError`

#### Scenario: Fan-out without merge
- **WHEN** a graph contains a FAN_OUT node but no MERGE node is reachable from any of its branches
- **THEN** it SHALL raise `GraphValidationError` with message "FAN_OUT node '{id}' has no reachable MERGE node"

#### Scenario: Merge without fan-out
- **WHEN** a graph contains a MERGE node but no FAN_OUT node is upstream
- **THEN** it SHALL raise `GraphValidationError` with message "MERGE node '{id}' has no upstream FAN_OUT node"

#### Scenario: Fan-out branches must match merge
- **WHEN** a FAN_OUT node has 3 branches but the downstream MERGE node's config lists a different fan_out_source
- **THEN** it SHALL raise `GraphValidationError`

#### Scenario: Interrupt list references non-existent node
- **WHEN** `interrupt_before: ["ghost"]` is declared and "ghost" is not a declared node ID
- **THEN** the compiler SHALL raise `GraphValidationError` with field indicating the interrupt list path

#### Scenario: Task mode with interrupt lists
- **WHEN** `execution_mode="task"` is passed to `compile()` and the graph declares a non-empty `interrupt_before` or `interrupt_after` list
- **THEN** the compiler SHALL raise `GraphValidationError` with message indicating declarative interrupts are forbidden in task mode

#### Scenario: Task mode with SUGGESTION node
- **WHEN** `execution_mode="task"` is passed to `compile()` and the graph contains a SUGGESTION node
- **THEN** the compiler SHALL raise `GraphValidationError` with message indicating SUGGESTION nodes are forbidden in task mode

#### Scenario: Conversational mode allows all node types
- **WHEN** `execution_mode="conversational"` is passed to `compile()` and the graph contains SUGGESTION nodes and non-empty interrupt lists
- **THEN** the compiler SHALL compile successfully without raising mode-related errors

#### Scenario: No execution mode defaults to conversational
- **WHEN** `execution_mode` is not provided to `compile()`
- **THEN** the compiler SHALL default to `"conversational"` behavior and allow all node types

#### Scenario: Routing config validation for intent mode
- **WHEN** a CONDITION node has `routing_mode: "intent"` but no `routing_config.intent_patterns`
- **THEN** the compiler SHALL raise `GraphValidationError` indicating intent routing requires intent_patterns

#### Scenario: Routing config validation for dynamic mode
- **WHEN** a CONDITION node has `routing_mode: "dynamic"` but no `routing_config.candidate_agents`
- **THEN** the compiler SHALL raise `GraphValidationError` indicating dynamic routing requires candidate_agents

#### Scenario: Dynamic routing candidates must reference existing nodes
- **WHEN** a CONDITION node has `routing_mode: "dynamic"` and `candidate_agents: ["agent_a", "nonexistent"]`
- **THEN** the compiler SHALL raise `GraphValidationError` indicating candidate "nonexistent" is not a declared node

#### Scenario: Invalid routing mode rejected
- **WHEN** a CONDITION node has `routing_mode: "unknown"`
- **THEN** `parse_graph()` SHALL raise `GraphValidationError`

#### Scenario: Channel access warnings logged
- **WHEN** a node declares `channels.readable: ["nonexistent"]` and "nonexistent" is not in graph `state`
- **THEN** the compiler SHALL log a WARNING about undeclared channel access

## ADDED Requirements

### Requirement: Declarative interrupt lists
The Graph DSL document SHALL accept optional top-level `interrupt_before` and `interrupt_after` arrays of node IDs. `parse_graph()` SHALL propagate both lists into the parsed graph configuration, the compiler SHALL carry them onto the compiled graph, and `CompiledGraph.to_json()` SHALL roundtrip both lists so persisted graph definitions keep their interrupt configuration. Absent lists SHALL behave as empty（不产生任何暂停点）。

#### Scenario: Parse and compile with interrupt lists
- **WHEN** a DSL document declares `"interrupt_before": ["review_gate"], "interrupt_after": ["planner"]` and both node IDs exist
- **THEN** the parser SHALL accept the document and the compiled graph SHALL expose both lists for the runtime

#### Scenario: to_json roundtrips interrupt lists
- **WHEN** a compiled graph carrying `interrupt_before` / `interrupt_after` is serialized via `to_json()` and re-parsed
- **THEN** both lists SHALL be preserved unchanged

#### Scenario: Absent lists default to empty
- **WHEN** a DSL document omits both `interrupt_before` and `interrupt_after`
- **THEN** parsing and compilation SHALL succeed and the compiled graph SHALL carry empty lists

## MODIFIED Requirements

### Requirement: Graph DSL parser validates against JSON Schema
The `parse_graph()` function SHALL accept a JSON string or dict and validate it against `schemas/graph-dsl.schema.json`. The schema SHALL include `"persistent"` as an optional boolean property on channel definitions. The parser SHALL auto-migrate deprecated `"persistent_topic"` to `"topic"` with `persistent=True`. The schema SHALL also support `routing_mode` and `routing_config` fields on CONDITION node config, and `"dynamic_handoff"` as a valid edge trigger value.

#### Scenario: Persistent channel in JSON
- **WHEN** `parse_graph()` encounters a channel definition with `"type": "topic", "persistent": true`
- **THEN** it SHALL create `ChannelDef(type=ChannelType.TOPIC, persistent=True)`

#### Scenario: Deprecated persistent_topic
- **WHEN** `parse_graph()` encounters `"type": "persistent_topic"`
- **THEN** it SHALL create `ChannelDef(type=ChannelType.TOPIC, persistent=True)` and log a deprecation warning

#### Scenario: Custom registered type
- **WHEN** `parse_graph()` encounters `"type": "priority_queue"` and "priority_queue" is registered in ChannelTypeRegistry
- **THEN** it SHALL create `ChannelDef(type=ChannelType("priority_queue"))` without error

#### Scenario: Unknown type
- **WHEN** `parse_graph()` encounters `"type": "unknown"` and "unknown" is NOT in the registry
- **THEN** it SHALL raise `GraphValidationError` with field pointing to the channel type

#### Scenario: Routing mode in DSL
- **WHEN** `parse_graph()` encounters a CONDITION node with `routing_mode: "intent"` and `routing_config`
- **THEN** it SHALL parse the routing config into the NodeConfig without error

#### Scenario: Dynamic handoff trigger in DSL
- **WHEN** `parse_graph()` encounters an edge with `trigger: "dynamic_handoff"`
- **THEN** the resulting `Edge` SHALL have `trigger="dynamic_handoff"` set

### Requirement: map-over-channel 声明式扇出配置

Graph DSL SHALL 支持在 CONDITION 节点配置 `fanout` 声明动态扇出:`{"fanout": {"over": <通道名>, "target": <目标节点ID>, "state_key": <载荷键名>, "max_fanout": <可选上限>}}`。执行时对该通道当前值的每个元素产出一个派发包(目标为 `target`,载荷为 `{state_key: 元素}`)。v1 SHALL NOT 提供 LLM 规划器模式或 worker 自主派发(`Command(goto=[...])`)——两者显式留待后续(5.11 / 独立变更)。

#### Scenario: fanout 配置的节点产出计划
- **WHEN** 图 DSL 含 `{"type": "condition", "config": {"fanout": {"over": "queries", "target": "collect", "state_key": "query"}}}`
- **THEN** 该节点执行时 SHALL 按 `queries` 的元素个数产出派发包,无需任何 Python 边函数

#### Scenario: 未配置 fanout 的 CONDITION 节点行为不变
- **WHEN** CONDITION 节点无 `fanout` 配置
- **THEN** 其执行与 `_route` 边解析 SHALL 与既有语义逐字一致

### Requirement: 扇出配置编译期校验

编译器 SHALL 对 `fanout` 配置做编译期校验:`over` 通道必须存在于图 state;`target` 必须是图内存在的节点;`state_key` 必须非空;`max_fanout`(若提供)必须为正整数。校验失败 SHALL 报 `GraphValidationError` 并指明字段。`fanout` 配置于非 CONDITION 类型节点时 SHALL 被拒绝。

#### Scenario: 目标节点不存在
- **WHEN** `fanout.target` 指向图中不存在的节点
- **THEN** 编译 SHALL 失败,错误信息 SHALL 指明 `fanout.target` 字段与缺失的节点 ID

#### Scenario: over 通道未声明
- **WHEN** `fanout.over` 引用未在图 state 声明的通道
- **THEN** 编译 SHALL 失败,错误信息 SHALL 指明 `fanout.over` 字段


## Requirements

