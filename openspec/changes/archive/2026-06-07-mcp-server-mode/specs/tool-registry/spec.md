## MODIFIED Requirements

### Requirement: ToolRegistry routes tool execution by source type
The system SHALL provide a `ToolRegistry` service in `services/tool/registry.py` that accepts a tool name, arguments, and optional context, looks up the tool definition by source type, and routes execution to the appropriate executor.

#### Scenario: Built-in tool execution
- **WHEN** `registry.execute("web_search", {"query": "test"}, context)` is called and a builtin tool named "web_search" exists
- **THEN** the registry SHALL route to `BuiltInToolExecutor` and return the tool's result

#### Scenario: Custom tool execution (not yet implemented)
- **WHEN** `registry.execute("my_tool", args, context)` is called and the tool has `source="custom"`
- **THEN** the registry SHALL raise `NotImplementedError` with message indicating custom tool execution is not yet available

#### Scenario: MCP tool execution via MCP Client
- **WHEN** `registry.execute("mcp_tool", args, context)` is called and the tool has `source="mcp"` with a non-null `mcp_server` field
- **THEN** the registry SHALL route the call through `MCPClientManager.call_tool(server_name=tool.mcp_server, tool_name=tool.mcp_tool_name, arguments=args)` and return the result

#### Scenario: MCP tool with no connected server
- **WHEN** `registry.execute("mcp_tool", args, context)` is called and the tool's `mcp_server` value has no active connection in `MCPClientManager`
- **THEN** the registry SHALL raise `ConnectionError` with message indicating the MCP server is not connected

#### Scenario: Unknown tool name
- **WHEN** `registry.execute("nonexistent", args, context)` is called and no tool with that name exists
- **THEN** the registry SHALL raise `ValueError` with message indicating the tool was not found
