## MODIFIED Requirements

### Requirement: NodeType enum defines 6 execution behaviors
The `NodeType` enum SHALL define: CONVERSATION, TOOL_CALL, CONDITION, AGENT, KNOWLEDGE_RETRIEVAL, VARIABLE_SET, FAN_OUT, MERGE.

The `CollaborationEventType` enum SHALL be defined in `engine/eventbus.py` with values: AGENT_MESSAGE, AGENT_REQUEST, AGENT_RESPONSE, TASK_ASSIGNED, TASK_COMPLETED, NEGOTIATION_PROPOSAL, NEGOTIATION_ACCEPT, NEGOTIATION_REJECT, DEBATE_ARGUMENT, DEBATE_REBUTTAL, DEBATE_CONCLUSION.

This is a new enum alongside the existing `EventType` in `engine/eventstore.py` — the existing EventType SHALL NOT be modified.

#### Scenario: Conversation node
- **WHEN** a node has type CONVERSATION
- **THEN** the worker SHALL invoke an LLM with the current channel state

#### Scenario: Agent node
- **WHEN** a node has type AGENT
- **THEN** the worker SHALL delegate execution to a sub-graph representing another agent

#### Scenario: Collaboration event type
- **WHEN** `CollaborationEventType.AGENT_MESSAGE` is referenced
- **THEN** it SHALL equal the string `"AGENT_MESSAGE"`
