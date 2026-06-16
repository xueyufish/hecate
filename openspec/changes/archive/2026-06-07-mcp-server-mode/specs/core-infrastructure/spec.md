## ADDED Requirements

### Requirement: MCP Server and Client configuration settings
The `Settings` class SHALL include the following MCP-related settings:
- `MCP_SERVER_ENABLED: bool` (default: `False`) — enable/disable MCP Server
- `MCP_SERVER_HOST: str` (default: `"0.0.0.0"`) — MCP Server bind host
- `MCP_SERVER_PORT: int` (default: `8000`) — MCP Server bind port (informational when mounted)
- `MCP_AUTH_TYPE: str` (default: `"api_key"`) — auth mode for MCP requests (`api_key`, `jwt`, `none`)
- `MCP_TRANSPORT: str` (default: `"http"`) — transport mode for MCP Server (`http` for Streamable HTTP)
- `MCP_CLIENT_TIMEOUT: int` (default: `30`) — timeout in seconds for MCP Client operations

#### Scenario: Default MCP configuration
- **WHEN** no MCP-related environment variables are set
- **THEN** `MCP_SERVER_ENABLED=False`, `MCP_AUTH_TYPE="api_key"`, `MCP_TRANSPORT="http"`, `MCP_CLIENT_TIMEOUT=30`

#### Scenario: Enable MCP server
- **WHEN** `MCP_SERVER_ENABLED=true`
- **THEN** the MCP Server ASGI app SHALL be mounted at `/mcp` on the FastAPI application

#### Scenario: Custom auth type
- **WHEN** `MCP_AUTH_TYPE=none`
- **THEN** MCP tool calls SHALL skip API key validation
