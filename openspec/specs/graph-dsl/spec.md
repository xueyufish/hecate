## ADDED Requirements

### Requirement: Graph DSL parser validates against JSON Schema
The `parse_graph()` function SHALL accept a JSON string or dict and validate it against `schemas/graph-dsl.schema.json`.

#### Scenario: Valid JSON graph
- **WHEN** `parse_graph('{"version":"1.0","nodes":{...},"edges":[...]}')` is called with valid input
- **THEN** it SHALL return a `GraphConfig` with typed `ChannelDef`, `NodeConfig`, and `Edge` objects

#### Scenario: Invalid JSON
- **WHEN** `parse_graph("not json")` is called
- **THEN** it SHALL raise `GraphValidationError` with field=None

#### Scenario: Schema validation failure
- **WHEN** the input fails JSON Schema validation
- **THEN** it SHALL raise `GraphValidationError` with `field` set to the dotted JSON path of the invalid element

### Requirement: Compiler validates entry point, edges, and handoff cycles
The `GraphCompiler.compile()` SHALL perform three validation stages before producing a `CompiledGraph`.

#### Scenario: Entry point not found
- **WHEN** the declared entry point references a non-existent node
- **THEN** it SHALL raise `GraphValidationError` with field="entry"

#### Scenario: Edge target references non-existent node
- **WHEN** an edge target is neither a declared node ID nor a sentinel (`__start__`, `__end__`)
- **THEN** it SHALL raise `GraphValidationError` with field indicating the edge path

#### Scenario: Unreachable nodes logged as warning
- **WHEN** nodes exist that are not reachable from the entry point via BFS
- **THEN** the compiler SHALL log a WARNING with the unreachable node IDs but SHALL NOT raise an error

### Requirement: Compiler detects handoff cycles
The compiler SHALL validate that handoff edges (trigger="handoff") connect only agent-type nodes and contain no cycles.

#### Scenario: Handoff between non-agent nodes
- **WHEN** a handoff edge source or target is not an AGENT-type node
- **THEN** it SHALL raise `GraphValidationError`

#### Scenario: Circular handoff chain
- **WHEN** handoff edges form a cycle (A→B→C→A)
- **THEN** it SHALL raise `GraphValidationError` with message "Circular handoff chain detected"

### Requirement: Three-layer agent template generates standard graph
The `build_three_layer_graph()` function SHALL produce a GraphConfig with Guard→Planner→Sub-Agent pattern.

#### Scenario: Template structure
- **WHEN** `build_three_layer_graph(guard_model, planner_model, sub_agent_model)` is called
- **THEN** the graph SHALL contain 5 nodes: guard (CONVERSATION), planner (CONVERSATION), sub_agent (AGENT), tool_call (TOOL_CALL), check_tools (CONDITION)

#### Scenario: Template edges
- **WHEN** the template is compiled
- **THEN** edges SHALL be: guard→planner, planner→check_tools, check_tools→{true: tool_call, false: sub_agent}, tool_call→planner, sub_agent→__end__

#### Scenario: Template state channels
- **WHEN** the template is used
- **THEN** it SHALL define "messages" (TOPIC) and "context" (LAST_VALUE) channels
