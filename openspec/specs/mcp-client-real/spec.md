## ADDED Requirements

### Requirement: Real MCP Client using official SDK
The system SHALL provide a production MCP Client using the official `mcp` Python SDK (`modelcontextprotocol/python-sdk`) that supports Streamable HTTP and stdio transports for connecting to external MCP servers.

#### Scenario: Connect to remote MCP server via Streamable HTTP
- **WHEN** `HecateMCPClient.connect(server_url="http://remote-server:8000/mcp", transport="http")` is called
- **THEN** the client establishes a `ClientSession` using `streamable_http_client` and can list and call tools

#### Scenario: Connect to local MCP server via stdio
- **WHEN** `HecateMCPClient.connect(command="python", args=["server.py"], transport="stdio")` is called
- **THEN** the client launches the subprocess and establishes a `ClientSession` using `stdio_client`

#### Scenario: List tools from connected server
- **WHEN** `client.list_tools()` is called after successful connection
- **THEN** the client returns a list of tool definitions with name, description, and inputSchema

#### Scenario: Call tool on connected server
- **WHEN** `client.call_tool(tool_name="search", arguments={"query": "test"})` is called
- **THEN** the client sends a `tools/call` request to the MCP server and returns the result

#### Scenario: Disconnect from server
- **WHEN** `client.disconnect()` is called
- **THEN** the client closes the session and cleans up resources

### Requirement: MCP Client manager for multiple servers
The system SHALL provide `MCPClientManager` that manages connections to multiple MCP servers simultaneously, supporting tool discovery and execution across all connected servers.

#### Scenario: Add and connect to a server
- **WHEN** `manager.add_server("my-server", server_url="http://localhost:8000/mcp")` is called
- **THEN** the manager creates a `HecateMCPClient`, connects, and stores it under the `"my-server"` key

#### Scenario: Discover tools from all servers
- **WHEN** `manager.discover_tools()` is called
- **THEN** the manager aggregates tools from all connected servers, tagging each with its source server name

#### Scenario: Call tool on specific server
- **WHEN** `manager.call_tool(server_name="my-server", tool_name="search", arguments={"q": "test"})` is called
- **THEN** the manager routes the call to the specified server and returns the result

### Requirement: MCP Client connection configuration
The system SHALL provide `MCP_CLIENT_TIMEOUT: int` (default: `30`) setting for client connection and tool call timeouts.

#### Scenario: Timeout on slow server
- **WHEN** an MCP server does not respond within `MCP_CLIENT_TIMEOUT` seconds
- **THEN** the client raises a `TimeoutError` and the calling tool receives an error response
