## ADDED Requirements

### Requirement: Convert React Flow state to Graph DSL
The system SHALL provide a function `flowToDsl(nodes: Node[], edges: Edge[]) → GraphDsl` that converts the visual canvas state into a valid Graph DSL JSON object conforming to `graph-dsl.schema.json`.

#### Scenario: Convert simple two-node flow
- **WHEN** canvas has a start node connected to a conversation node via one edge
- **THEN** the output DSL contains `{"version": "1.0", "name": "...", "nodes": {"node_1": {"type": "conversation", "config": {...}}}, "edges": [{"source": "__start__", "target": "node_1"}]}`

#### Scenario: Convert condition with branching edges
- **WHEN** canvas has a condition node with two outgoing edges labeled "true" and "false"
- **THEN** the output DSL edge has `target: {"true": "node-a", "false": "node-b"}`

### Requirement: Convert Graph DSL to React Flow state
The system SHALL provide a function `dslToFlow(dsl: GraphDsl) → {nodes: Node[], edges: Edge[]}` that converts a Graph DSL JSON into React Flow node and edge arrays with auto-layout positioning.

#### Scenario: Convert DSL with positioned nodes
- **WHEN** DSL contains nodes with `_position` metadata
- **THEN** nodes are placed at those positions in the canvas

#### Scenario: Convert DSL without positions (auto-layout)
- **WHEN** DSL contains nodes without `_position` metadata
- **THEN** nodes are arranged using a top-to-bottom DAG layout algorithm

### Requirement: Validate DSL on conversion
`flowToDsl` SHALL validate the output against `graph-dsl.schema.json` and return validation errors if the DSL is invalid.

#### Scenario: Validate missing required node config
- **WHEN** a node on canvas has no type configured
- **THEN** `flowToDsl` returns an error list including "Node 'x' is missing required field 'type'"

#### Scenario: Validate unreachable nodes
- **WHEN** a node exists on canvas with no path from __start__
- **THEN** `flowToDsl` returns a warning "Node 'x' is unreachable from entry point" but does not fail

### Requirement: Round-trip fidelity
Converting DSL → Flow → DSL SHALL produce semantically equivalent DSL (node types, configs, edges match). Visual metadata (positions, viewport) MAY differ.

#### Scenario: Round-trip preserves node configs
- **WHEN** DSL with a conversation node having `{"model": "gpt-4o", "system_prompt": "You are helpful"}` is converted to flow and back
- **THEN** the resulting DSL contains the same node with the same model and system_prompt values
