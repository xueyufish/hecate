## ADDED Requirements

### Requirement: AgentDefinition dataclass for per-invocation configuration
The engine SHALL define an `AgentDefinition` dataclass in `engine/agent_tool.py` with fields: `agent_id` (UUID), `description` (str, visible to LLM), `prompt_override` (str | None, default None), `tools` (list[str] | None, default None), `disallowed_tools` (list[str], default `["agent_execute"]`), `skills` (list[str] | None, default None), `model_override` (str | None, default None), `context_mode` (Literal["inherited", "isolated"], default "inherited"), `max_turns` (int | None, default None), `timeout_seconds` (float | None, default None).

#### Scenario: Minimal AgentDefinition
- **WHEN** `AgentDefinition(agent_id=UUID("..."), description="Research assistant")` is created
- **THEN** `tools` SHALL be None (inherit all), `disallowed_tools` SHALL be `["agent_execute"]`, `context_mode` SHALL be `"inherited"`, all optional fields SHALL be None

#### Scenario: Full AgentDefinition with whitelist
- **WHEN** `AgentDefinition(agent_id=UUID("..."), description="Researcher", tools=["web_search", "read"], disallowed_tools=["agent_execute"], context_mode="isolated", max_turns=5, timeout_seconds=60.0)` is created
- **THEN** all fields SHALL be set as specified

### Requirement: AgentTool wraps agent as callable tool
The engine SHALL define an `AgentTool` class that implements the tool interface (name, description, execute) and wraps an `AgentDefinition` for invocation via `EnginePort.agent_execute()`.

#### Scenario: AgentTool as tool definition
- **WHEN** `AgentTool(definition=AgentDefinition(agent_id=agent_uuid, description="Searches the web for information"))` is created
- **THEN** the tool SHALL have name derived from the agent definition (e.g., `"agent_{name}"`) and description matching the AgentDefinition's description

#### Scenario: AgentTool execution
- **WHEN** `agent_tool.execute({"task": "Find latest AI papers"})` is called
- **THEN** it SHALL call `port.agent_execute(agent_id, messages=[{"role": "user", "content": "Find latest AI papers"}], channel_snapshot=..., context=...)` and return the agent's response as the tool result

#### Scenario: AgentTool with context isolation
- **WHEN** an AgentTool with `context_mode="isolated"` is executed
- **THEN** the sub-agent SHALL receive only the task message, not the parent's full conversation history

#### Scenario: AgentTool with inherited context
- **WHEN** an AgentTool with `context_mode="inherited"` is executed
- **THEN** the sub-agent SHALL receive the parent's messages channel content as its conversation history

### Requirement: Tool whitelist/blacklist resolution
When resolving the effective tool list for an AgentTool invocation, the system SHALL apply: if `tools` is not None → use whitelist minus disallowed_tools; if `tools` is None → inherit parent's tools minus disallowed_tools.

#### Scenario: Whitelist minus blacklist
- **WHEN** `tools=["web_search", "read", "agent_execute"]` and `disallowed_tools=["agent_execute"]`
- **THEN** the effective tool list SHALL be `["web_search", "read"]`

#### Scenario: Inherit minus blacklist
- **WHEN** `tools=None` and parent has tools `["web_search", "read", "write", "agent_execute"]` and `disallowed_tools=["agent_execute", "write"]`
- **THEN** the effective tool list SHALL be `["web_search", "read"]`

#### Scenario: Empty whitelist
- **WHEN** `tools=[]` (empty list)
- **THEN** the sub-agent SHALL have no tools available

### Requirement: AgentTool timeout enforcement
When `timeout_seconds` is set on an AgentDefinition, the AgentTool execution SHALL enforce the timeout using `asyncio.wait_for`.

#### Scenario: Execution within timeout
- **WHEN** agent execution completes in 10 seconds and `timeout_seconds=60.0`
- **THEN** the result SHALL be returned normally

#### Scenario: Execution exceeds timeout
- **WHEN** agent execution exceeds `timeout_seconds=5.0`
- **THEN** the tool SHALL raise `asyncio.TimeoutError` wrapped in a tool execution error

### Requirement: AgentTool max_turns enforcement
When `max_turns` is set on an AgentDefinition, the system SHALL pass it to `agent_execute()` context to limit the sub-agent's execution loop.

#### Scenario: Max turns respected
- **WHEN** `max_turns=3` is set and the sub-agent would normally loop 10 times
- **THEN** the sub-agent SHALL stop after 3 turns and return its current response
