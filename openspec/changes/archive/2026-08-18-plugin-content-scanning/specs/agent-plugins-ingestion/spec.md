## MODIFIED Requirements

### Requirement: Package record and provenance
Each installed package SHALL persist exactly one PluginModel row with `type="agent-plugin"`, status, workspace scope, provenance fields `origin` (source descriptor including git pin triple when applicable), `content_hash`, and `scan_result` (carrying the content-scan verdict, findings, and scanner version once feature 5.13a is active), and a manifest JSON containing the full plugin.json plus a component inventory listing each skill and MCP server with its import outcome. The install directory and the database row SHALL be created and removed as a pair; at startup, install directories without a matching row SHALL be removed as orphans.

#### Scenario: Package row carries provenance
- **WHEN** a package installs successfully
- **THEN** its PluginModel row contains origin, content_hash, and a component inventory with per-component outcomes

#### Scenario: Orphan directory cleaned at startup
- **WHEN** the managed install directory contains a package directory with no matching PluginModel row
- **THEN** the system removes the orphan directory at startup and logs the cleanup

### Requirement: Scan stage slot
The ingestion pipeline SHALL invoke the content scanner between validation and persistence, and re-invoke it on enable. The scan stage SHALL produce a verdict of allow, warn, or block: a block verdict SHALL abort the install before any row or directory is persisted, and a scanner failure SHALL abort the install (fail-closed). Successful installs SHALL persist the scan verdict, findings, and scanner version into `scan_result`; enable-time rescans SHALL apply per the plugin-content-scanning capability.

#### Scenario: Block verdict aborts install
- **WHEN** a package's content scan yields a block verdict
- **THEN** the install is rejected with findings in the error and no PluginModel row or directory is persisted

#### Scenario: Successful install persists scan result
- **WHEN** a package installs with a warn verdict
- **THEN** the PluginModel row's `scan_result` carries the verdict, findings, and scanner version

#### Scenario: Scan failure aborts install
- **WHEN** the content scanner raises an unexpected error during install
- **THEN** the install is rejected with a scanner-failure error

### Requirement: Master switch
All ingestion entry points (REST API and CLI) SHALL be gated by a configuration switch. With feature 5.13a shipped, the switch SHALL default to on; setting it off SHALL make install endpoints return a feature-disabled error and the CLI report the feature disabled. The switch SHALL serve as an emergency kill-switch at all times.

#### Scenario: Default-on allows install
- **WHEN** the configuration switch is at its default (on) and an install is requested
- **THEN** the install proceeds through validation and scanning

#### Scenario: Kill-switch rejects install
- **WHEN** the configuration switch is off and an install is requested
- **THEN** the API returns a feature-disabled error and no state changes
