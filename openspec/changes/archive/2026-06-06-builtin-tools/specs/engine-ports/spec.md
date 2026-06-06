## MODIFIED Requirements

### Requirement: Tool execution
- **WHEN** `tool_execute(name, args, context)` is called
- **THEN** it SHALL route the call through ToolRegistry, which resolves the tool by name and source type, executes it via the appropriate executor, and returns the tool's result

#### Scenario: Tool execution via registry
- **WHEN** `tool_execute("web_search", {"query": "test"}, context)` is called
- **THEN** the adapter SHALL delegate to `ToolRegistry.execute("web_search", {"query": "test"}, context)` and return the registry's result

#### Scenario: Tool not found
- **WHEN** `tool_execute("nonexistent", args, context)` is called and the tool does not exist
- **THEN** it SHALL raise `ValueError` with message indicating the tool was not found
