## ADDED Requirements

### Requirement: Handoff tool auto-injection
The system SHALL inject a `handoff_to_agent` tool into an Agent's tool list when the graph DSL defines a handoff edge originating from that agent node. The tool SHALL accept a `target` parameter specifying the target agent node ID.

#### Scenario: Handoff tool injected when handoff edge exists
- **WHEN** a graph DSL contains an edge with `type: "handoff"` from agent node "triage" to agent node "billing"
- **THEN** the system injects `handoff_to_agent` tool into the "triage" agent's tool list with `target` parameter accepting values `["billing"]`

#### Scenario: No handoff tool when no handoff edges
- **WHEN** a graph DSL has no edges with `type: "handoff"` from an agent node
- **THEN** the system does NOT inject `handoff_to_agent` tool into that agent's tool list

### Requirement: Handoff tool execution produces Command(goto)
The system SHALL execute the `handoff_to_agent` tool by returning a `WorkerResult` with `Command(goto=target_node_id)`. The Pregel runtime SHALL resolve the next node as the target agent node.

#### Scenario: Successful handoff execution
- **WHEN** the LLM calls `handoff_to_agent(target="billing")`
- **THEN** the worker returns `Command(goto="billing")` and the Pregel runtime executes the "billing" agent node in the next superstep

#### Scenario: Handoff with conversation context
- **WHEN** the LLM calls `handoff_to_agent(target="specialist")` and the current channel has `messages` containing the conversation history
- **THEN** the target agent node receives the conversation messages as its input context

### Requirement: Handoff edge type in Graph DSL
The system SHALL support an optional `type` field on edges in the Graph DSL. When `type: "handoff"`, the edge represents a control transfer (handoff). When absent, the edge is a standard data-flow edge.

#### Scenario: Parse graph with handoff edges
- **WHEN** `parse_graph()` receives a DSL with `{"source": "agent_a", "target": "agent_b", "type": "handoff"}`
- **THEN** the resulting `Edge` has `trigger="handoff"` set

#### Scenario: Compile graph with handoff edges
- **WHEN** `GraphCompiler.compile()` processes a graph containing handoff edges
- **THEN** the compiled graph preserves the handoff edge trigger and the compiler validates that both source and target are agent-type nodes

### Requirement: Handoff cycle detection
The system SHALL detect and reject circular handoff chains during graph compilation. A circular handoff chain exists when a sequence of handoff edges forms a cycle.

#### Scenario: Circular handoff detected
- **WHEN** a graph contains handoff edges A→B, B→C, C→A forming a cycle
- **THEN** the compiler raises a `GraphCompilationError` with a message listing the cycle

#### Scenario: Non-circular handoff accepted
- **WHEN** a graph contains handoff edges A→B, B→C (no cycle)
- **THEN** the compiler accepts the graph without error
