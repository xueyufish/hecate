## ADDED Requirements

### Requirement: PluginLifecycle protocol
The system SHALL define a `PluginLifecycle` Protocol that plugins MAY implement for lifecycle hooks. The protocol MUST include:
- `on_load() -> None` — called when plugin is registered
- `on_unload() -> None` — called when plugin is unregistered

#### Scenario: Plugin implements lifecycle
- **WHEN** a plugin implements the PluginLifecycle protocol
- **THEN** PluginRegistry calls on_load() after registration and on_unload() after unregistration

#### Scenario: Plugin does not implement lifecycle
- **WHEN** a plugin does not implement the PluginLifecycle protocol
- **THEN** PluginRegistry logs a debug message and continues without calling lifecycle hooks

### Requirement: PluginRegistry lifecycle integration
The system SHALL integrate PluginLifecycle hooks into the PluginRegistry registration and unregistration flow.

#### Scenario: Registration triggers on_load
- **WHEN** a plugin implementing PluginLifecycle is registered
- **THEN** PluginRegistry calls on_load() on the plugin instance

#### Scenario: Unregistration triggers on_unload
- **WHEN** a plugin implementing PluginLifecycle is unregistered
- **THEN** PluginRegistry calls on_unload() on the plugin instance before removal

#### Scenario: Lifecycle hook exception handling
- **WHEN** a lifecycle hook raises an exception
- **THEN** PluginRegistry logs the error and continues (does not propagate)
