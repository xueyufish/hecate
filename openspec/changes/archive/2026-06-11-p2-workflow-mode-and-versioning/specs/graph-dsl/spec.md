## MODIFIED Requirements

### Requirement: Compiler validates entry point, edges, and handoff cycles
The `GraphCompiler.compile()` SHALL perform validation stages before producing a `CompiledGraph`: entry point, edges, handoff cycles, fan-out/merge structural constraints, and execution-mode-aware node restrictions. When `execution_mode="task"` is passed to compile(), the compiler SHALL reject graphs containing INTERRUPT or SUGGESTION node types by raising `GraphValidationError`.

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
