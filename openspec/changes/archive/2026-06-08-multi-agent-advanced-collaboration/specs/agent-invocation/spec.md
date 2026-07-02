## MODIFIED Requirements

### Requirement: Agent-as-Tool dynamic registration
The system SHALL support an `invocation_mode` field in AGENT node configuration. When `invocation_mode: "tool"`, the target agent SHALL be registered as a callable tool in the parent agent's tool list during execution, using an `AgentDefinition` for permission scoping when provided.

#### Scenario: Agent exposed as tool with AgentDefinition
- **WHEN** an AGENT node has config `{"agent_id": "uuid-of-specialist", "invocation_mode": "tool", "agent_definition": {"tools": ["web_search"], "context_mode": "isolated"}}`
- **THEN** the parent agent's tool list includes a tool named `agent_{specialist_name}` with the AgentDefinition's tool filter and context isolation applied

#### Scenario: Agent exposed as tool without AgentDefinition (existing behavior)
- **WHEN** an AGENT node has config `{"agent_id": "uuid-of-specialist", "invocation_mode": "tool"}` (no agent_definition)
- **THEN** the parent agent's tool list includes a tool named `agent_{specialist_name}` with full tool inheritance (existing behavior unchanged)

#### Scenario: Agent tool invocation with filtered tools
- **WHEN** the parent LLM calls the `agent_{specialist_name}` tool with arguments `{"task": "analyze this data"}`
- **THEN** the system executes the specialist agent with only the tools specified in the AgentDefinition, not the specialist's full tool list
