## MODIFIED Requirements

### Requirement: MCP server registry
The system SHALL maintain a registry of MCP servers with their capabilities (tools/resources/prompts). Servers register when their plugin is enabled, unregister when disabled. Registration sources are: (a) a plugin with a single `entry: mcp://endpoint`, and (b) an agent-plugin package (`type="agent-plugin"`) whose manifest carries mcp.json components — each such server registers under the name `<plugin-name>__<server-name>` in the installing workspace's scope. At platform startup, the system SHALL replay registration for every enabled plugin source so registrations survive restarts. The registry supports capability-based discovery — clients can query which servers provide specific tools.

#### Scenario: Server registered on plugin enable
- **WHEN** a plugin with `entry: mcp://endpoint` is enabled
- **THEN** the system registers the MCP server in the registry without connecting

#### Scenario: Server unregistered on plugin disable
- **WHEN** an MCP server plugin is disabled
- **THEN** the system unregisters the server, closes any active connections, and clears the tool cache

#### Scenario: Manifest components registered with prefixed names
- **WHEN** an agent-plugin package `docs-helper` with mcp.json entries `search` and `fetch` is enabled
- **THEN** the system registers two servers named `docs-helper__search` and `docs-helper__fetch` scoped to the package's workspace

#### Scenario: Startup replay restores registrations
- **WHEN** the platform restarts with an enabled agent-plugin package carrying mcp.json components
- **THEN** the system re-registers its servers into the registry without user action

#### Scenario: Uninstall unregisters manifest servers
- **WHEN** an agent-plugin package with registered mcp.json servers is uninstalled
- **THEN** the system unregisters all of its `<plugin-name>__*` servers and clears their tool caches

#### Scenario: Capability discovery
- **WHEN** a client queries available tools across all registered MCP servers
- **THEN** the system returns cached tool lists from all connected servers
