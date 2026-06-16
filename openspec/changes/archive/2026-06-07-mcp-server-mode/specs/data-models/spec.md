## ADDED Requirements

### Requirement: ToolModel supports MCP server connection metadata
The `ToolModel` SHALL include `mcp_server` (String, nullable) and `mcp_tool_name` (String, nullable) fields to identify the originating MCP server and tool name for `source="mcp"` tools. These fields already exist in the current schema; no migration is required.

#### Scenario: MCP tool with server reference
- **WHEN** a tool with `source="mcp"` is created with `mcp_server="my-remote-server"` and `mcp_tool_name="search"`
- **THEN** the ToolRegistry SHALL use these fields to route execution to the correct MCP client connection
