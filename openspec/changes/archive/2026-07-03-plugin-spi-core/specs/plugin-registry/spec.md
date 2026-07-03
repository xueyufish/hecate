## ADDED Requirements

### Requirement: PluginRegistry registration
The system SHALL provide a `PluginRegistry` class that manages plugin registration and discovery. The registry MUST support:
- `register(manifest: PluginManifest, plugin: Any) -> None` — register a plugin with its manifest
- `unregister(type: str, name: str) -> None` — remove a registered plugin
- `get_by_type(type: str) -> dict[str, Any]` — get all plugins of a given type, keyed by name
- `get_by_name(type: str, name: str) -> Any | None` — get a specific plugin by type and name
- `list_all() -> dict[str, dict[str, Any]]` — get all registered plugins grouped by type

#### Scenario: Register a plugin
- **WHEN** a developer calls `registry.register(manifest, plugin_instance)`
- **THEN** the plugin is stored and retrievable by type and name

#### Scenario: Register duplicate plugin
- **WHEN** a developer registers a plugin with the same type and name as an existing plugin
- **THEN** the new plugin replaces the old one

#### Scenario: Unregister a plugin
- **WHEN** a developer calls `registry.unregister(type, name)`
- **THEN** the plugin is removed and no longer retrievable

#### Scenario: Get plugins by type
- **WHEN** a developer calls `registry.get_by_type("evaluator")`
- **THEN** a dictionary of all evaluator plugins keyed by name is returned

#### Scenario: Get specific plugin
- **WHEN** a developer calls `registry.get_by_name("evaluator", "faithfulness")`
- **THEN** the specific evaluator plugin is returned, or None if not found

#### Scenario: List all plugins
- **WHEN** a developer calls `registry.list_all()`
- **THEN** a dictionary of all plugins grouped by type is returned

### Requirement: PluginRegistry thread safety
The system SHALL ensure PluginRegistry is thread-safe for concurrent registration and lookup operations.

#### Scenario: Concurrent registration
- **WHEN** multiple threads register plugins simultaneously
- **THEN** all registrations complete without data corruption

#### Scenario: Concurrent lookup during registration
- **WHEN** one thread registers a plugin while another thread queries by type
- **THEN** the query returns a consistent snapshot without raising exceptions
