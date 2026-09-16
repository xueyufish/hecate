## MODIFIED Requirements

### Requirement: EnginePort agent_execute method
The system SHALL add an `agent_execute` method to `EnginePort` that accepts an agent_id, messages, channel_snapshot, and optional context, and returns a dict containing the agent's response. The concrete implementation SHALL load the agent's configured tools, query knowledge bases, apply guard hooks (PreLLMHook/PostLLMHook), and call context_assemble before invoking the LLM — matching the full pipeline used by LLMWorker for CONVERSATION nodes. When the optional `agent_definition` carries a `prompt_override`, the top-level invocation SHALL use the override value as the system prompt in place of the agent's configured prompt — the same override semantics already applied on the agent-as-tool delegation path. When `agent_definition` is absent or carries no `prompt_override`, behavior SHALL be unchanged.

#### Scenario: Agent execution via port
- **WHEN** `agent_execute(agent_id=UUID("..."), messages=[{"role": "user", "content": "hello"}], channel_snapshot={})` is called
- **THEN** the port resolves the AgentModel by ID, loads the agent's configured tools, queries the agent's knowledge bases, applies PreLLMHook, calls context_assemble, invokes the LLM with tools, applies PostLLMHook, and returns `{"response": "...", "usage": {...}}`

#### Scenario: Agent not found
- **WHEN** `agent_execute(agent_id=UUID("nonexistent"), ...)` is called
- **THEN** the port raises `ValueError` with message indicating the agent was not found

#### Scenario: Agent execution with tool filtering
- **WHEN** `agent_execute` is called with an `agent_definition` that specifies `tools: ["web_search", "lookup_invoice"]`
- **THEN** only the tools listed in the AgentDefinition are passed to the LLM, not the agent's full tool list

#### Scenario: Agent execution with prompt override
- **WHEN** `agent_execute` is called with an `agent_definition` whose `prompt_override` is set to a non-empty template value
- **THEN** the LLM invocation uses the override value as the system prompt instead of the agent's configured prompt, while tools, knowledge bases, model configuration, and hooks are resolved from the agent configuration unchanged

#### Scenario: Agent execution without prompt override
- **WHEN** `agent_execute` is called without an `agent_definition`, or with an `agent_definition` whose `prompt_override` is null
- **THEN** the system prompt is resolved from the agent configuration exactly as before this capability existed

#### Scenario: Agent execution with knowledge bases
- **WHEN** the agent has knowledge bases configured in its AgentModel
- **THEN** the system queries those knowledge bases using the user's messages as context and injects relevant chunks into the LLM context

#### Scenario: PreLLMHook blocks agent execution
- **WHEN** PreLLMHook returns action=BLOCK for the agent's messages
- **THEN** the system returns a response indicating the request was blocked, without invoking the LLM
