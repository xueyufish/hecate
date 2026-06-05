## Requirements

### Requirement: NodeType enum defines 6 execution behaviors
The `NodeType` enum SHALL define: CONVERSATION, TOOL_CALL, CONDITION, AGENT, KNOWLEDGE_RETRIEVAL, VARIABLE_SET, FAN_OUT, MERGE.

#### Scenario: Conversation node
- **WHEN** a node has type CONVERSATION
- **THEN** the worker SHALL invoke an LLM with the current channel state

#### Scenario: Condition node
- **WHEN** a node has type CONDITION
- **THEN** the worker SHALL evaluate an expression against channel state to determine routing

#### Scenario: Agent node
- **WHEN** a node has type AGENT
- **THEN** the worker SHALL delegate execution to a sub-graph representing another agent

#### Scenario: Fan-out node
- **WHEN** a node has type FAN_OUT
- **THEN** the runtime SHALL dispatch all branch nodes concurrently without invoking a worker on the FAN_OUT node itself

#### Scenario: Merge node
- **WHEN** a node has type MERGE
- **THEN** the worker SHALL collect results from all fan-out branch sub-channels and produce an aggregated output
