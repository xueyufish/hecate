## ADDED Requirements

### Requirement: Plugin manifest loading
The system SHALL parse `plugin.yaml` files into `PluginManifest` objects. The manifest SHALL support the following fields: `name` (required), `version` (required), `type` (required), `api_version` (required), `min_platform_version` (required), `description`, `entry` (required), `permissions` (list), `config_schema` (JSON Schema object). The loader SHALL validate that all required fields are present and raise a validation error if any are missing.

#### Scenario: Valid plugin.yaml loaded
- **WHEN** a `plugin.yaml` file with all required fields is loaded
- **THEN** the system returns a `PluginManifest` with all fields populated

#### Scenario: Missing required field
- **WHEN** a `plugin.yaml` file is missing the `entry` field
- **THEN** the system raises a `ValueError` with a message indicating the missing field

#### Scenario: Invalid YAML syntax
- **WHEN** a `plugin.yaml` file contains invalid YAML syntax
- **THEN** the system raises a `yaml.YAMLError` and logs the error

### Requirement: Plugin directory discovery
The system SHALL scan a configurable plugins directory (default: `plugins/`) at startup to discover plugin packages. Each plugin package SHALL be a subdirectory containing a `plugin.yaml` file. The system SHALL log discovered plugins and skip directories without a `plugin.yaml`.

#### Scenario: Discover plugins at startup
- **WHEN** the application starts with `plugins/` containing `plugin-a/plugin.yaml` and `plugin-b/plugin.yaml`
- **THEN** the system discovers both plugins and attempts to load each manifest

#### Scenario: Skip directories without plugin.yaml
- **WHEN** `plugins/` contains a `README.md` file but no `plugin.yaml`
- **THEN** the system skips that entry and logs a debug message

### Requirement: Plugin compatibility validation
The system SHALL validate `api_version` and `min_platform_version` from the manifest against the current platform version. A plugin whose `min_platform_version` is greater than the current platform version SHALL be rejected with an error message.

#### Scenario: Compatible plugin version
- **WHEN** a plugin declares `min_platform_version: "0.7.0"` and the current platform version is `"0.8.0"`
- **THEN** the system accepts the plugin and proceeds with registration

#### Scenario: Incompatible plugin version
- **WHEN** a plugin declares `min_platform_version: "0.9.0"` and the current platform version is `"0.8.0"`
- **THEN** the system rejects the plugin, logs an error, and marks it with status `error` in the database

### Requirement: Extended plugin lifecycle
The system SHALL support extended lifecycle hooks beyond the existing `on_load` / `on_unload`: `on_enable` (called when a plugin transitions to enabled state), `on_disable` (called when transitioning to disabled state), and `on_config_change` (called when plugin configuration is updated). Plugins that do not implement these hooks SHALL continue to function without error.

#### Scenario: Plugin with all lifecycle hooks
- **WHEN** a plugin implementing `on_enable`, `on_disable`, and `on_config_change` is enabled
- **THEN** the system calls `on_enable` after updating the plugin status to `enabled` in the database

#### Scenario: Plugin without extended hooks
- **WHEN** a plugin that only implements `on_load` and `on_unload` is enabled
- **THEN** the system updates the status to `enabled` without error and skips the unimplemented hooks

#### Scenario: Config change triggers hook
- **WHEN** a plugin's configuration is updated via the API and the plugin implements `on_config_change`
- **THEN** the system calls `on_config_change` with the new configuration dictionary after persisting to the database

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

### Requirement: Two-layer plugin scope
The system SHALL support two plugin scopes: platform-level (globally available, `workspace_id=None`) and workspace-level (per-workspace, `workspace_id` set). Platform-level plugins SHALL be visible to all workspaces. Workspace-level plugins SHALL only be visible within their workspace.

#### Scenario: Platform-level plugin visible to all workspaces
- **WHEN** a plugin is registered with `workspace_id=None`
- **THEN** the plugin appears in the plugin list for every workspace

#### Scenario: Workspace-level plugin isolated
- **WHEN** workspace A has a plugin with `workspace_id=A` and workspace B requests the plugin list
- **THEN** workspace B's plugin list does not include workspace A's plugin

### Requirement: Plugin configuration management
The system SHALL support plugin configuration via `config_schema` (JSON Schema) declared in `plugin.yaml`. Configuration values SHALL be stored in the `PluginModel.config` JSON column. The system SHALL validate configuration values against `config_schema` before persisting. The system SHALL inject configuration values into the plugin instance at load time.

#### Scenario: Valid configuration save
- **WHEN** an administrator saves configuration `{"api_key": "xxx", "threshold": 0.8}` for a plugin with a matching `config_schema`
- **THEN** the system validates the config against the schema, persists it to the database, and calls `on_config_change`

#### Scenario: Invalid configuration rejected
- **WHEN** an administrator saves configuration missing a required field defined in `config_schema`
- **THEN** the system rejects the save with a validation error and does not persist

#### Scenario: Config injection at load time
- **WHEN** a plugin with `config_schema` and stored config values is loaded
- **THEN** the system injects the stored config values into the plugin instance

### Requirement: Plugin permission enforcement
The system SHALL parse permission declarations from `plugin.yaml` (`permissions` field). Plugins SHALL only access resources matching their declared permissions. The system SHALL log warnings when a plugin attempts an undeclared permission.

#### Scenario: Plugin with declared permissions
- **WHEN** a plugin declares `permissions: ["network:https"]` and makes an HTTPS request
- **THEN** the system allows the operation

#### Scenario: Plugin with undeclared permission
- **WHEN** a plugin declares `permissions: ["network:https"]` but attempts filesystem write
- **THEN** the system logs a warning indicating undeclared permission `filesystem:write`

### Requirement: Entry loading via python: prefix
The system SHALL load plugins with `entry: python:module:Class` format using `importlib.import_module()` to import the module and instantiate the class, subject to the T0 trust gate for in-process plugin entries. The loaded instance SHALL be registered with `PluginRegistry`. When the trust gate rejects the entry, the system SHALL skip the plugin without crashing the discovery loop: log an ERROR naming the T0 policy as the rejection reason, count the plugin in the discovery error count, and neither register nor persist it.

#### Scenario: Load first-party Python plugin
- **WHEN** a plugin manifest declares `entry: python:hecate.plugins.my_plugin:MyToolPlugin`
- **THEN** the system imports the module, instantiates `MyToolPlugin`, and registers it with `PluginRegistry` in both SaaS and self-hosted modes

#### Scenario: Rejected entry skipped without crashing discovery
- **WHEN** a discovered plugin's `python:` entry is rejected by the T0 trust gate
- **THEN** the system logs an ERROR naming the T0 policy, counts the plugin as an error, continues discovering remaining plugins, and does not register or persist the rejected plugin

#### Scenario: Invalid Python entry
- **WHEN** a plugin manifest declares `entry: python:nonexistent:Class`
- **THEN** the system catches `ImportError`, logs the error, and marks the plugin with status `error`

### Requirement: T0 trust gate for in-process plugin entries
The loader SHALL apply a fail-closed trust gate before importing any `python:` entry: a module is loadable in-process only when it is first-party (`hecate` exactly, or any module under the `hecate.` namespace) or when it matches a configured allowlist prefix under self-hosted deployment. Prefix matching SHALL respect module-name segment boundaries: a configured prefix matches a module only when it equals the module or is an ancestor package of it (prefix `mycompany.` matches `mycompany.tools.x` but not `mycompanyevil.x`). In SaaS mode (`SAAS_MODE=true`) non-first-party modules SHALL be rejected outright and the allowlist SHALL NOT grant exceptions. In self-hosted mode non-first-party modules SHALL be rejected by default (default-deny) and loadable only when the module matches a prefix in `PLUGIN_PYTHON_ENTRY_ALLOWLIST`. Third-party content SHALL NOT be able to inject modules into the first-party namespace: plugin directories are not placed on the module search path.

#### Scenario: SaaS rejects non-first-party python entry
- **WHEN** `SAAS_MODE=true` and a discovered plugin declares `entry: python:my_plugin:MyToolPlugin`
- **THEN** the loader rejects the entry before import, regardless of any allowlist configuration

#### Scenario: Self-hosted default-deny for non-first-party entry
- **WHEN** `SAAS_MODE=false`, `PLUGIN_PYTHON_ENTRY_ALLOWLIST` is empty, and a discovered plugin declares `entry: python:my_plugin:MyToolPlugin`
- **THEN** the loader rejects the entry before import

#### Scenario: Self-hosted allowlist prefix grants loading
- **WHEN** `SAAS_MODE=false`, `PLUGIN_PYTHON_ENTRY_ALLOWLIST=["mycompany."]`, and a discovered plugin declares `entry: python:mycompany.tools.weather:WeatherPlugin`
- **THEN** the loader imports the module and loads the plugin

#### Scenario: Allowlist prefix does not cross segment boundaries
- **WHEN** `SAAS_MODE=false`, `PLUGIN_PYTHON_ENTRY_ALLOWLIST=["mycompany."]`, and a discovered plugin declares `entry: python:mycompanyevil.exfil:EvilPlugin`
- **THEN** the loader rejects the entry before import

#### Scenario: First-party namespace root is loadable
- **WHEN** a discovered plugin declares an entry whose module is exactly `hecate`
- **THEN** the loader permits the import in both SaaS and self-hosted modes

### Requirement: Install-time rejection of untrusted python entries
The plugin installation path SHALL apply the same T0 trust gate when installing a `.hecate-plugin` bundle whose manifest declares a `python:` entry: installation SHALL fail with an error that identifies the trust gate and names the remediation (remove the plugin or, on self-hosted, add its module prefix to the allowlist), and the just-extracted plugin directory SHALL be removed so no rejected artifact remains on disk. Bundles whose `python:` entry passes the gate, and bundles without `python:` entries, SHALL install normally.

#### Scenario: Bundle with non-first-party python entry fails installation
- **WHEN** a `.hecate-plugin` bundle declares `entry: python:my_plugin:MyToolPlugin` and the gate rejects the module under the current deployment mode
- **THEN** installation fails with an error naming the trust gate and the remediation, and the extracted plugin directory is removed

#### Scenario: Bundle with first-party python entry installs
- **WHEN** a `.hecate-plugin` bundle declares `entry: python:hecate.plugins.my_plugin:MyToolPlugin`
- **THEN** installation succeeds

### Requirement: Runtime dependency installation disabled in SaaS mode
In SaaS mode the installer SHALL NOT execute runtime dependency installation (`pip install` of a plugin's `requirements.txt`): the step SHALL be skipped with a WARNING log. In self-hosted mode runtime dependency installation SHALL continue to run, with imported third-party modules still subject to the T0 trust gate at load time.

#### Scenario: SaaS skips requirements installation
- **WHEN** `SAAS_MODE=true` and a bundle containing `requirements.txt` is installed
- **THEN** the installer skips dependency installation and logs a WARNING, and the installation itself proceeds

#### Scenario: Self-hosted installs requirements
- **WHEN** `SAAS_MODE=false` and a bundle containing `requirements.txt` is installed
- **THEN** the installer runs dependency installation as before

### Requirement: Entry loading via mcp:// prefix
The system SHALL load plugins with `entry: mcp://endpoint` format by creating an MCP client connection to the specified endpoint. The MCP server's discovered tools SHALL be registered with `PluginRegistry` as plugin instances.

#### Scenario: Load MCP plugin
- **WHEN** a plugin manifest declares `entry: mcp://http://localhost:8080`
- **THEN** the system connects via MCP Client, discovers available tools, and registers them with `PluginRegistry`

#### Scenario: MCP endpoint unreachable
- **WHEN** a plugin manifest declares `entry: mcp://http://localhost:9999` and the endpoint is unreachable
- **THEN** the system catches the connection error, logs it, and marks the plugin with status `error`

### Requirement: Plugin management REST API
The system SHALL expose REST API endpoints for plugin management: `GET /api/plugins` (list plugins, filterable by workspace and type), `GET /api/plugins/{id}` (get plugin detail), `POST /api/plugins/{id}/enable` (enable plugin), `POST /api/plugins/{id}/disable` (disable plugin), `PUT /api/plugins/{id}/config` (update plugin configuration).

#### Scenario: List plugins
- **WHEN** a client requests `GET /api/plugins`
- **THEN** the system returns a list of all plugins with their status, type, version, and configuration

#### Scenario: Enable plugin
- **WHEN** a client requests `POST /api/plugins/{id}/enable`
- **THEN** the system transitions the plugin to `enabled` status and calls `on_enable`

#### Scenario: Update plugin config
- **WHEN** a client requests `PUT /api/plugins/{id}/config` with valid configuration
- **THEN** the system validates against `config_schema`, persists, and calls `on_config_change`

### Requirement: Frontend plugin management page
The system SHALL provide a web UI plugin management page accessible from the sidebar navigation. The page SHALL display a plugin list with status badges (enabled/disabled/error), type, and version. Clicking a plugin SHALL open a detail page with enable/disable toggle and a configuration form auto-generated from the plugin's `config_schema`.

#### Scenario: Plugin list page
- **WHEN** the administrator navigates to the plugins page
- **THEN** the page displays all plugins with status badges, type, version, and enable/disable toggles

#### Scenario: Config form auto-generation
- **WHEN** the administrator opens a plugin detail page for a plugin with `config_schema` defining `api_key` (string, secret) and `threshold` (number, 0-1)
- **THEN** the page renders a password input for `api_key` and a number input with bounds 0-1 for `threshold`

#### Scenario: MCP endpoint management
- **WHEN** the administrator opens a plugin detail page for a plugin with `entry: mcp://...`
- **THEN** the page displays the MCP endpoint URL and connection status
