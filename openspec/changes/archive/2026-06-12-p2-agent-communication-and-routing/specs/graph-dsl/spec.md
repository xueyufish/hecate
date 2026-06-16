## MODIFIED Requirements

### Requirement: Compiler validates entry point, edges, and handoff cycles
The `GraphCompiler.compile()` SHALL perform validation stages before producing a `CompiledGraph`: entry point, edges, handoff cycles, fan-out/merge structural constraints, execution-mode-aware node restrictions, channel access validation, and routing config validation. When `execution_mode="task"` is passed to compile(), the compiler SHALL reject graphs containing INTERRUPT or SUGGESTION node types by raising `GraphValidationError`.

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

#### Scenario: Task mode with INTERRUPT node
- **WHEN** `execution_mode="task"` is passed to `compile()` and the graph contains an INTERRUPT node
- **THEN** the compiler SHALL raise `GraphValidationError` with message indicating INTERRUPT nodes are forbidden in task mode

#### Scenario: Task mode with SUGGESTION node
- **WHEN** `execution_mode="task"` is passed to `compile()` and the graph contains a SUGGESTION node
- **THEN** the compiler SHALL raise `GraphValidationError` with message indicating SUGGESTION nodes are forbidden in task mode

#### Scenario: Conversational mode allows all node types
- **WHEN** `execution_mode="conversational"` is passed to `compile()` and the graph contains INTERRUPT and SUGGESTION nodes
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
