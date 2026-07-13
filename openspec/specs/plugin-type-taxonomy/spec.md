## ADDED Requirements

### Requirement: Tool Plugin ABC
The system SHALL define `ToolPluginABC` as the abstract base class for tool-type plugins. A Tool Plugin extends Agent capabilities with callable functions. The ABC SHALL require `name`, `description` properties and an async `execute` method that accepts parameters and returns a result dict.

#### Scenario: Tool plugin registered and callable
- **WHEN** a plugin with `type: tool` is loaded via plugin.yaml and its entry class implements `ToolPluginABC`
- **THEN** the system registers the tool in PluginRegistry and makes it callable by Agents

#### Scenario: Tool plugin with invalid interface
- **WHEN** a plugin declares `type: tool` but its entry class does not implement `ToolPluginABC`
- **THEN** the loader rejects the plugin with a validation error

### Requirement: Extension Plugin ABC
The system SHALL define `ExtensionPluginABC` as the abstract base class for extension-type plugins. An Extension Plugin injects logic into the agent execution flow via optional callback methods: `on_pre_llm`, `on_post_llm`, `on_pre_tool`, `on_post_tool`. A plugin need only implement the callbacks it cares about — unimplemented callbacks are silently skipped.

#### Scenario: Extension plugin with all callbacks
- **WHEN** a plugin implements all four callback methods and is enabled
- **THEN** the system calls each callback at the corresponding execution stage

#### Scenario: Extension plugin with partial callbacks
- **WHEN** a plugin implements only `on_pre_tool` and is enabled
- **THEN** the system calls `on_pre_tool` before each tool execution and skips the other three callbacks without error

#### Scenario: Extension plugin bridges to existing Guardrail Hooks
- **WHEN** an Extension plugin's `on_pre_llm` returns a `GuardrailResult` with action BLOCK
- **THEN** the system blocks the LLM call, matching the behavior of the existing `PreLLMHook`

### Requirement: Trigger Plugin ABC
The system SHALL define `TriggerPluginABC` as the abstract base class for trigger-type plugins. A Trigger Plugin responds to external events. The ABC SHALL support three trigger sources: `webhook` (HTTP POST), `schedule` (cron expression), and `event` (internal event bus). Each trigger source has a corresponding async handler method.

#### Scenario: Webhook trigger fires
- **WHEN** an HTTP POST request arrives at the trigger's webhook URL
- **THEN** the system calls the plugin's webhook handler with the request payload

#### Scenario: Schedule trigger fires
- **WHEN** the cron expression for a schedule trigger matches the current time
- **THEN** the system calls the plugin's schedule handler

### Requirement: Model Plugin ABC
The system SHALL define `ModelPluginABC` as the abstract base class for model-type plugins. A Model Plugin provides a custom LLM inference backend not covered by LiteLLM. The ABC SHALL require `invoke` (text generation) and `embed` (embedding generation) async methods.

#### Scenario: Model plugin provides custom inference
- **WHEN** a Model Plugin is enabled and an Agent requests a model provided by this plugin
- **THEN** the system routes the inference request to the plugin's `invoke` method

### Requirement: Existing ABC plugin.yaml support
The system SHALL support loading plugins that implement existing ABCs (`ChannelABC`, `EvaluatorABC`, `AuthProviderABC`, `SecretProviderABC`) via plugin.yaml. The loader SHALL validate that the entry class implements the correct ABC for the declared `type` field.

#### Scenario: Third-party Channel plugin loaded
- **WHEN** a plugin declares `type: channel` and its entry class implements `ChannelABC`
- **THEN** the system loads and registers the channel adapter via PluginRegistry

#### Scenario: Third-party Evaluator plugin loaded
- **WHEN** a plugin declares `type: evaluator` and its entry class implements `EvaluatorABC`
- **THEN** the system loads and registers the evaluator via PluginRegistry

### Requirement: hecate.plugin SDK module
The system SHALL provide a `hecate.plugin` Python module that re-exports all 8 plugin type ABCs and provides helper utilities: `PluginContext` (config injection + permission checking), `register()` (simplified registration helper). Developers import from this single module.

#### Scenario: Developer imports plugin base class
- **WHEN** a developer writes `from hecate.plugin import ToolPluginABC`
- **THEN** the import resolves and the class is available for subclassing

#### Scenario: PluginContext injects config
- **WHEN** a plugin's `on_config_change` is called with a PluginContext
- **THEN** the plugin can access `ctx.config` for its configuration values and `ctx.check_permission("network:https")` for permission validation

### Requirement: hecate plugin init CLI
The system SHALL provide a `hecate plugin init <name> --type <type>` CLI command that scaffolds a new plugin project directory with plugin.yaml, Python entry module, and test skeleton. The `--type` flag accepts all 8 plugin types.

#### Scenario: Scaffold a tool plugin
- **WHEN** a developer runs `hecate plugin init my-tool --type tool`
- **THEN** the system creates `my-tool/` directory with `plugin.yaml`, `__init__.py` (containing `ToolPluginABC` subclass), and `test_my_tool.py`

#### Scenario: Invalid type rejected
- **WHEN** a developer runs `hecate plugin init my-plugin --type unknown`
- **THEN** the system rejects with an error listing valid types

### Requirement: Hot-reload during development
The system SHALL support hot-reload of plugins during development. When a plugin.yaml or plugin source file changes, the system detects the change via file watcher, unloads the old plugin, and reloads the new version without restarting the application.

#### Scenario: Plugin source file modified
- **WHEN** a plugin's Python source file is modified while hot-reload is enabled
- **THEN** the system reloads the plugin within 2 seconds and logs the reload event

#### Scenario: Hot-reload disabled in production
- **WHEN** the application runs with `HOT_RELOAD=false` (default)
- **THEN** file changes do not trigger reload

### Requirement: Install-time API surface validation
The system SHALL validate plugin API compatibility at install/load time, beyond 5.5's basic version string check. Validation SHALL verify that the plugin's entry class has the expected method signatures for its declared type (e.g., a Tool Plugin must have `execute` method with correct parameters).

#### Scenario: Valid plugin passes validation
- **WHEN** a plugin with correct method signatures is loaded
- **THEN** validation passes and the plugin is registered

#### Scenario: Missing required method detected
- **WHEN** a plugin declares `type: tool` but its class has no `execute` method
- **THEN** validation fails with an error describing the missing method

### Requirement: API-type plugin online creation UI
The system SHALL provide a web UI for creating simple plugins (Tool and Trigger types) without writing code. The UI SHALL present a form where users define tool name, description, input/output parameters, and optionally an API endpoint URL. For Trigger type, users define webhook path or cron expression.

#### Scenario: Create a tool plugin via UI
- **WHEN** an administrator fills the plugin creation form with tool name "search-web", description "Web search tool", and API endpoint URL
- **THEN** the system creates a PluginModel with the tool definition and makes it available in the plugin list

#### Scenario: Create a webhook trigger via UI
- **WHEN** an administrator fills the trigger creation form with webhook path "/trigger/my-webhook" and selects a target workflow
- **THEN** the system creates a PluginModel with the trigger definition and registers the webhook endpoint
