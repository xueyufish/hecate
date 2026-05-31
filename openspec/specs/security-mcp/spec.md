## ADDED Requirements

### Requirement: LLMGuardScanner provides input/output safety scanning
The `LLMGuardScanner` SHALL scan prompts and outputs for safety issues using LLM Guard with lazy-loaded scanners and mock fallback.

#### Scenario: Scanner disabled
- **WHEN** `enabled=False` or `LLM_GUARD_ENABLED=False`
- **THEN** `scan_prompt()` and `scan_output()` SHALL return `ScanResult(is_safe=True, score=1.0, issues=[])`

#### Scenario: Mock scanner when llm_guard not installed
- **WHEN** llm_guard is not installed
- **THEN** the scanner SHALL use mock scan that detects "hack" and "exploit" keywords

#### Scenario: Prompt scanners
- **WHEN** llm_guard is installed
- **THEN** prompt scanning SHALL use PromptInjection (threshold=0.5), Anonymize, and Secrets scanners

#### Scenario: Output scanners
- **WHEN** llm_guard is installed
- **THEN** output scanning SHALL use Toxicity scanner (threshold=0.7)

#### Scenario: Scan returns issues
- **WHEN** a scanner detects a risk
- **THEN** the result SHALL include the scanner name and risk score in issues list

### Requirement: PIIAnonymizer provides reversible PII masking
The `PIIAnonymizer` SHALL detect and mask PII (email, phone, credit card, SSN, IP address) using regex patterns with reversible placeholder mapping.

#### Scenario: Anonymize text with email
- **WHEN** `anonymize("Contact john@example.com")` is called
- **THEN** it SHALL return `AnonymizedText` with email replaced by `[EMAIL_1]` and mappings preserving the original

#### Scenario: Deanonymize restores original
- **WHEN** `deanonymize(anonymized_text)` is called
- **THEN** it SHALL restore all placeholders to their original values

#### Scenario: Has PII detection
- **WHEN** `has_pii(text)` is called
- **THEN** it SHALL return True if any PII pattern matches

### Requirement: MCPClient connects to MCP servers for tool discovery
The `MCPClient` SHALL connect to MCP servers, discover tools via `tools/list`, and execute tools via `tools/call`.

#### Scenario: Connect to server
- **WHEN** `connect()` is called
- **THEN** it SHALL set `_connected=True` and return True

#### Scenario: List tools
- **WHEN** `list_tools()` is called
- **THEN** it SHALL auto-connect if not connected and return the tool list

#### Scenario: Call tool
- **WHEN** `call_tool(tool_name, arguments)` is called
- **THEN** it SHALL auto-connect if not connected and return a result dict

### Requirement: MCPManager manages multiple MCP connections
The `MCPManager` SHALL manage multiple MCP clients, aggregate tool discovery across servers, and route tool calls.

#### Scenario: Add server
- **WHEN** `add_server(url)` is called
- **THEN** it SHALL create/connect an MCPClient and store it by URL

#### Scenario: Discover tools from all servers
- **WHEN** `discover_tools()` is called
- **THEN** it SHALL aggregate tools from all connected servers, tagging each with `mcp_server` URL

#### Scenario: Call tool on specific server
- **WHEN** `call_tool(server_url, tool_name, arguments)` is called
- **THEN** it SHALL route to the client for that server; raise ValueError if no client exists

### Requirement: MCPToolSync converts MCP tools to Hecate format
The `MCPToolSync` SHALL discover tools from MCP servers and convert them to Hecate tool format with source="mcp".

#### Scenario: Sync tools from server
- **WHEN** `sync_tools(server_url)` is called
- **THEN** it SHALL connect to the server, list tools, and convert each to Hecate format

#### Scenario: Tool conversion
- **WHEN** an MCP tool is converted
- **THEN** it SHALL have source="mcp", risk_level="LOW", approval_required=False, and mcp_server/mcp_tool_name set from the source
