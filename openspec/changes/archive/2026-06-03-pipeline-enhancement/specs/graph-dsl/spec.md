## MODIFIED Requirements

### Requirement: Compiler validates entry point, edges, and handoff cycles
The `GraphCompiler.compile()` SHALL perform four validation stages before producing a `CompiledGraph`: entry point, edges, handoff cycles, and fan-out/merge structural constraints.

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

### Requirement: Graph DSL parser validates against JSON Schema
The `parse_graph()` function SHALL accept a JSON string or dict and validate it against `schemas/graph-dsl.schema.json`. The schema SHALL include "fan-out" and "merge" as valid node type enum values.

#### Scenario: Valid JSON graph
- **WHEN** `parse_graph('{"version":"1.0","nodes":{...},"edges":[...]}')` is called with valid input
- **THEN** it SHALL return a `GraphConfig` with typed `ChannelDef`, `NodeConfig`, and `Edge` objects

#### Scenario: Fan-out node in JSON
- **WHEN** `parse_graph(...)` encounters a node with `"type": "fan-out"`
- **THEN** it SHALL create a `NodeConfig` with `NodeType.FAN_OUT`

#### Scenario: Merge node in JSON
- **WHEN** `parse_graph(...)` encounters a node with `"type": "merge"`
- **THEN** it SHALL create a `NodeConfig` with `NodeType.MERGE`
