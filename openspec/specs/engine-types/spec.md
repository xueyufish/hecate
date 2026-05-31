## ADDED Requirements

### Requirement: NodeType enum defines 6 execution behaviors
The `NodeType` enum SHALL define: CONVERSATION, TOOL_CALL, CONDITION, AGENT, KNOWLEDGE_RETRIEVAL, VARIABLE_SET.

#### Scenario: Conversation node
- **WHEN** a node has type CONVERSATION
- **THEN** the worker SHALL invoke an LLM with the current channel state

#### Scenario: Condition node
- **WHEN** a node has type CONDITION
- **THEN** the worker SHALL evaluate an expression against channel state to determine routing

#### Scenario: Agent node
- **WHEN** a node has type AGENT
- **THEN** the worker SHALL delegate execution to a sub-graph representing another agent

### Requirement: ChannelType enum defines 4 write semantics
The `ChannelType` enum SHALL define: LAST_VALUE (overwrite), TOPIC (append), PERSISTENT_TOPIC (append+persist), ACCUMULATOR (reduce).

#### Scenario: LAST_VALUE channel
- **WHEN** a value is written to a LAST_VALUE channel
- **THEN** the new value SHALL replace the old value entirely

#### Scenario: TOPIC channel
- **WHEN** a value is written to a TOPIC channel
- **THEN** the value SHALL be appended to a list

#### Scenario: ACCUMULATOR channel with "add" reduce
- **WHEN** a value is written to an ACCUMULATOR channel with reduce_fn="add"
- **THEN** the value SHALL be added to the current accumulator value

### Requirement: Command dataclass for control flow
The `Command` dataclass SHALL support goto, return_value, interrupt, and update directives.

#### Scenario: Interrupt command
- **WHEN** a worker returns `Command(interrupt={"question": "Approve?"})`
- **THEN** `is_interrupt()` SHALL return True and execution SHALL pause

#### Scenario: Goto command
- **WHEN** a worker returns `Command(goto="next_node")`
- **THEN** `is_goto()` SHALL return True and execution SHALL jump to the specified node

#### Scenario: Return command
- **WHEN** a worker returns `Command(return_value="done")`
- **THEN** `is_return()` SHALL return True and execution SHALL terminate

### Requirement: Edge supports conditional branching
The `Edge` dataclass SHALL accept either a string target (unconditional) or a dict target (conditional branching).

#### Scenario: Unconditional edge
- **WHEN** `Edge(source="a", target="b")` is defined
- **THEN** execution always flows from "a" to "b"

#### Scenario: Conditional edge
- **WHEN** `Edge(source="a", target={"true": "b", "false": "c"})` is defined
- **THEN** execution SHALL route to "b" or "c" based on the `_route` key in channel updates

### Requirement: CompiledGraph serializable to JSON
The `CompiledGraph` SHALL provide a `to_json()` method that produces a JSON-compatible dict.

#### Scenario: Serialize graph
- **WHEN** `compiled_graph.to_json()` is called
- **THEN** it SHALL return a dict with "version", "name", "state", "nodes", "edges", "entry" keys
