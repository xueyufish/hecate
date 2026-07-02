## ADDED Requirements

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

### Requirement: ToolRegistry uses in-memory builtin lookup with DB fallback
The registry SHALL maintain an in-memory set of builtin tool names for fast routing. For non-builtin tools, it SHALL query the `ToolModel` table by name and workspace_id.

#### Scenario: Builtin tool resolves without DB query
- **WHEN** a builtin tool name is looked up
- **THEN** the registry SHALL route directly to the builtin executor without querying the database

#### Scenario: Non-builtin tool queries database
- **WHEN** a non-builtin tool name is looked up
- **THEN** the registry SHALL query the `ToolModel` table to determine the source type

### Requirement: Built-in tool definitions are seeded to DB on startup
The system SHALL seed built-in tool definitions (name, description, parameters schema, source="builtin") to the `tools` database table during application startup.

#### Scenario: Fresh database gets all builtin tools
- **WHEN** the application starts with an empty tools table
- **THEN** all 5 builtin tools (web_search, read_file, write_file, list_files, execute_code) SHALL be inserted with `source="builtin"` and `workspace_id=00000000`

#### Scenario: Existing builtin tools are updated, not duplicated
- **WHEN** the application starts and builtin tools already exist in the database
- **THEN** the seed function SHALL update tool definitions (description, parameters) if they differ from code, without creating duplicates
