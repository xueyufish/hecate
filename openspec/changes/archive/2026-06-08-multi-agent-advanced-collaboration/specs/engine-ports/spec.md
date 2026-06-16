## MODIFIED Requirements

### Requirement: Optional agent_execute method for Multi-Agent
The `agent_execute()` method SHALL accept an optional `agent_definition: AgentDefinition | None = None` parameter. When provided, the execution SHALL use the AgentDefinition's overrides (tool filter, context mode, model override, max_turns) instead of the agent's defaults.

#### Scenario: Agent execution without definition (existing behavior)
- **WHEN** `agent_execute(agent_id=UUID("..."), messages=[...], channel_snapshot={})` is called without agent_definition
- **THEN** the execution SHALL proceed using the agent's configured tools, prompt, model, and context (existing behavior unchanged)

#### Scenario: Agent execution with definition override
- **WHEN** `agent_execute(agent_id=UUID("..."), messages=[...], channel_snapshot={}, agent_definition=AgentDefinition(agent_id=UUID("..."), tools=["web_search"], context_mode="isolated"))` is called
- **THEN** the execution SHALL use only `["web_search"]` as the tool list, create an isolated message context, and use the agent's configured model

#### Scenario: Unimplemented agent execution
- **WHEN** a concrete EnginePort does not override `agent_execute()`
- **THEN** calling it SHALL raise NotImplementedError with message "agent_execute requires a concrete EnginePort adapter"
