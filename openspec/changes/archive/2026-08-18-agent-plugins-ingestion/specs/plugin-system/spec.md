## MODIFIED Requirements

### Requirement: Plugin state persistence
The system SHALL persist plugin state in a `PluginModel` database table with the following attributes: `id` (UUID PK), `name`, `type`, `version`, `status` (enum: `installed`, `enabled`, `disabled`, `error`), `entry`, `manifest` (JSON), `config` (JSON), `workspace_id` (nullable UUID, None for platform-level plugins), provenance fields `origin` (nullable string; source descriptor including the git pin triple for git installs), `content_hash` (nullable string), `scan_result` (nullable JSON, reserved for feature 5.13a), and standard timestamps. The database SHALL be the runtime source of truth for plugin state. Rows with `type="agent-plugin"` SHALL use `entry=""`, and their `manifest` JSON SHALL carry the full plugin.json plus a component inventory (imported skills and MCP servers with per-component outcomes); their lifecycle (enable/disable/uninstall) is governed by the agent-plugins-ingestion capability.

#### Scenario: Register platform-level plugin
- **WHEN** a plugin is discovered from the global `plugins/` directory during startup
- **THEN** the system creates a `PluginModel` with `workspace_id=None` and `status=installed`

#### Scenario: Plugin status transitions
- **WHEN** a plugin in `installed` status is enabled via the API
- **THEN** the system updates `status` to `enabled` in the database and calls `on_enable`

#### Scenario: Agent-plugin row carries provenance
- **WHEN** an Agent Plugins package is installed
- **THEN** the system creates a `PluginModel` with `type="agent-plugin"`, `entry=""`, `origin` and `content_hash` populated, `scan_result=None`, and a manifest JSON containing plugin.json plus component inventory
