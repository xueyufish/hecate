## MODIFIED Requirements

### Requirement: Tool execution routes through ToolRegistry
- **WHEN** `tool_execute(name, args, context)` is called
- **THEN** it SHALL route the call through ToolRegistry, which resolves the tool by name and source type, executes it via the appropriate executor, and returns the tool's result

#### Scenario: Tool execution via registry
- **WHEN** `tool_execute("web_search", {"query": "test"}, context)` is called
- **THEN** the adapter SHALL delegate to `ToolRegistry.execute("web_search", {"query": "test"}, context)` and return the registry's result

#### Scenario: Tool not found
- **WHEN** `tool_execute("nonexistent", args, context)` is called and the tool does not exist
- **THEN** it SHALL raise `ValueError` with message indicating the tool was not found

### Requirement: Agent execution loads skills into system prompt
When `agent_execute()` is called for a sub-agent, the system SHALL load the agent's skills via `SkillLoader` and inject the formatted XML block into the system message alongside the agent's persona.

#### Scenario: Sub-agent with persona and skills
- **WHEN** `agent_execute(agent_id, messages, channel_snapshot)` is called for an agent with `persona="Expert coder"` and `skills=["code-review"]`
- **THEN** the system message SHALL be `"Expert coder\n\n<skills>\n<skill name=\"code-review\">\n...\n</skill>\n</skills>"` followed by the conversation messages

#### Scenario: Sub-agent with no skills
- **WHEN** `agent_execute()` is called for an agent with `skills=[]`
- **THEN** the system message SHALL be the agent's persona only, unchanged from current behavior
