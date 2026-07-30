## ADDED Requirements — 新增需求

### Requirement: Tool Plugin ABC — 需求：工具插件 ABC
The system SHALL define `ToolPluginABC` as the abstract base class for tool-type plugins. A Tool Plugin extends Agent capabilities with callable functions. The ABC SHALL require `name`, `description` properties and an async `execute` method that accepts parameters and returns a result dict.

系统应定义 `ToolPluginABC` 作为工具类型插件的抽象基类。工具插件通过可调用函数扩展 Agent 能力。ABC 应要求 `name`、`description` 属性和一个接受参数并返回结果字典的 async `execute` 方法。

#### Scenario: Tool plugin registered and callable — 场景：工具插件注册并可调用
- **WHEN** a plugin with `type: tool` is loaded via plugin.yaml and its entry class implements `ToolPluginABC`
- **THEN** the system registers the tool in PluginRegistry and makes it callable by Agents
- **当**通过 plugin.yaml 加载了 `type: tool` 的插件且其入口类实现了 `ToolPluginABC`
- **那么**系统在 PluginRegistry 中注册该工具并使其可被 Agent 调用

#### Scenario: Tool plugin with invalid interface — 场景：工具插件接口无效
- **WHEN** a plugin declares `type: tool` but its entry class does not implement `ToolPluginABC`
- **THEN** the loader rejects the plugin with a validation error
- **当**插件声明了 `type: tool` 但其入口类未实现 `ToolPluginABC`
- **那么**加载器拒绝该插件并返回验证错误

### Requirement: Extension Plugin ABC — 需求：扩展插件 ABC
The system SHALL define `ExtensionPluginABC` as the abstract base class for extension-type plugins. An Extension Plugin injects logic into the agent execution flow via optional callback methods: `on_pre_llm`, `on_post_llm`, `on_pre_tool`, `on_post_tool`. A plugin need only implement the callbacks it cares about — unimplemented callbacks are silently skipped.

系统应定义 `ExtensionPluginABC` 作为扩展类型插件的抽象基类。扩展插件通过可选回调方法将逻辑注入 Agent 执行流程：`on_pre_llm`、`on_post_llm`、`on_pre_tool`、`on_post_tool`。插件只需实现它关心的回调 — 未实现的回调被静默跳过。

#### Scenario: Extension plugin with all callbacks — 场景：具有所有回调的扩展插件
- **WHEN** a plugin implements all four callback methods and is enabled
- **THEN** the system calls each callback at the corresponding execution stage
- **当**插件实现所有四个回调方法并启用
- **那么**系统在相应的执行阶段调用每个回调

#### Scenario: Extension plugin with partial callbacks — 场景：具有部分回调的扩展插件
- **WHEN** a plugin implements only `on_pre_tool` and is enabled
- **THEN** the system calls `on_pre_tool` before each tool execution and skips the other three callbacks without error
- **当**插件仅实现 `on_pre_tool` 并启用
- **那么**系统在每次工具执行前调用 `on_pre_tool`，并跳过其他三个回调而不报错

#### Scenario: Extension plugin bridges to existing Guardrail Hooks — 场景：扩展插件桥接到现有 Guardrail 钩子
- **WHEN** an Extension plugin's `on_pre_llm` returns a `GuardrailResult` with action BLOCK
- **THEN** the system blocks the LLM call, matching the behavior of the existing `PreLLMHook`
- **当**扩展插件的 `on_pre_llm` 返回带有 BLOCK 动作的 `GuardrailResult`
- **那么**系统阻止 LLM 调用，与现有 `PreLLMHook` 的行为一致

### Requirement: Trigger Plugin ABC — 需求：触发器插件 ABC
The system SHALL define `TriggerPluginABC` as the abstract base class for trigger-type plugins. A Trigger Plugin responds to external events. The ABC SHALL support three trigger sources: `webhook` (HTTP POST), `schedule` (cron expression), and `event` (internal event bus). Each trigger source has a corresponding async handler method.

系统应定义 `TriggerPluginABC` 作为触发器类型插件的抽象基类。触发器插件响应外部事件。ABC 应支持三种触发源：`webhook`（HTTP POST）、`schedule`（cron 表达式）和 `event`（内部事件总线）。每种触发源都有对应的异步处理方法。

#### Scenario: Webhook trigger fires — 场景：Webhook 触发
- **WHEN** an HTTP POST request arrives at the trigger's webhook URL
- **THEN** the system calls the plugin's webhook handler with the request payload
- **当**HTTP POST 请求到达触发器的 webhook URL
- **那么**系统调用插件的 webhook 处理器，传入请求负载

#### Scenario: Schedule trigger fires — 场景：调度触发
- **WHEN** the cron expression for a schedule trigger matches the current time
- **THEN** the system calls the plugin's schedule handler
- **当**调度触发器的 cron 表达式与当前时间匹配
- **那么**系统调用插件的调度处理器

### Requirement: Model Plugin ABC — 需求：模型插件 ABC
The system SHALL define `ModelPluginABC` as the abstract base class for model-type plugins. A Model Plugin provides a custom LLM inference backend not covered by LiteLLM. The ABC SHALL require `invoke` (text generation) and `embed` (embedding generation) async methods.

系统应定义 `ModelPluginABC` 作为模型类型插件的抽象基类。模型插件提供 LiteLLM 未涵盖的自定义 LLM 推理后端。ABC 应要求 `invoke`（文本生成）和 `embed`（嵌入生成）异步方法。

#### Scenario: Model plugin provides custom inference — 场景：模型插件提供自定义推理
- **WHEN** a Model Plugin is enabled and an Agent requests a model provided by this plugin
- **THEN** the system routes the inference request to the plugin's `invoke` method
- **当**模型插件已启用且 Agent 请求该插件提供的模型
- **那么**系统将推理请求路由到插件的 `invoke` 方法

### Requirement: Existing ABC plugin.yaml support — 需求：现有 ABC 的 plugin.yaml 支持
The system SHALL support loading plugins that implement existing ABCs (`ChannelABC`, `EvaluatorABC`, `AuthProviderABC`, `SecretProviderABC`) via plugin.yaml. The loader SHALL validate that the entry class implements the correct ABC for the declared `type` field.

系统应支持通过 plugin.yaml 加载实现现有 ABC（`ChannelABC`、`EvaluatorABC`、`AuthProviderABC`、`SecretProviderABC`）的插件。加载器应验证入口类为其声明的 `type` 字段实现了正确的 ABC。

#### Scenario: Third-party Channel plugin loaded — 场景：加载第三方渠道插件
- **WHEN** a plugin declares `type: channel` and its entry class implements `ChannelABC`
- **THEN** the system loads and registers the channel adapter via PluginRegistry
- **当**插件声明 `type: channel` 且其入口类实现了 `ChannelABC`
- **那么**系统通过 PluginRegistry 加载并注册渠道适配器

#### Scenario: Third-party Evaluator plugin loaded — 场景：加载第三方评估器插件
- **WHEN** a plugin declares `type: evaluator` and its entry class implements `EvaluatorABC`
- **THEN** the system loads and registers the evaluator via PluginRegistry
- **当**插件声明 `type: evaluator` 且其入口类实现了 `EvaluatorABC`
- **那么**系统通过 PluginRegistry 加载并注册评估器

### Requirement: hecate.plugin SDK module — 需求：hecate.plugin SDK 模块
The system SHALL provide a `hecate.plugin` Python module that re-exports all 8 plugin type ABCs and provides helper utilities: `PluginContext` (config injection + permission checking), `register()` (simplified registration helper). Developers import from this single module.

系统应提供 `hecate.plugin` Python 模块，重新导出所有 8 种插件类型 ABC 并提供辅助工具：`PluginContext`（配置注入 + 权限检查）、`register()`（简化注册辅助）。开发者从这一个模块导入。

#### Scenario: Developer imports plugin base class — 场景：开发者导入插件基类
- **WHEN** a developer writes `from hecate.plugin import ToolPluginABC`
- **THEN** the import resolves and the class is available for subclassing
- **当**开发者编写 `from hecate.plugin import ToolPluginABC`
- **那么**导入解析成功，该类可用于子类化

#### Scenario: PluginContext injects config — 场景：PluginContext 注入配置
- **WHEN** a plugin's `on_config_change` is called with a PluginContext
- **THEN** the plugin can access `ctx.config` for its configuration values and `ctx.check_permission("network:https")` for permission validation
- **当**插件的 `on_config_change` 被调用并传入 PluginContext
- **那么**插件可以访问 `ctx.config` 获取其配置值，并使用 `ctx.check_permission("network:https")` 进行权限验证

### Requirement: hecate plugin init CLI — 需求：hecate plugin init CLI
The system SHALL provide a `hecate plugin init <name> --type <type>` CLI command that scaffolds a new plugin project directory with plugin.yaml, Python entry module, and test skeleton. The `--type` flag accepts all 8 plugin types.

系统应提供 `hecate plugin init <name> --type <type>` CLI 命令，用 plugin.yaml、Python 入口模块和测试骨架搭建新的插件项目目录。`--type` 标志接受所有 8 种插件类型。

#### Scenario: Scaffold a tool plugin — 场景：搭建工具插件
- **WHEN** a developer runs `hecate plugin init my-tool --type tool`
- **THEN** the system creates `my-tool/` directory with `plugin.yaml`, `__init__.py` (containing `ToolPluginABC` subclass), and `test_my_tool.py`
- **当**开发者运行 `hecate plugin init my-tool --type tool`
- **那么**系统创建 `my-tool/` 目录，包含 `plugin.yaml`、`__init__.py`（包含 `ToolPluginABC` 子类）和 `test_my_tool.py`

#### Scenario: Invalid type rejected — 场景：无效类型被拒绝
- **WHEN** a developer runs `hecate plugin init my-plugin --type unknown`
- **THEN** the system rejects with an error listing valid types
- **当**开发者运行 `hecate plugin init my-plugin --type unknown`
- **那么**系统拒绝并返回列出有效类型的错误

### Requirement: Hot-reload during development — 需求：开发期间的热重载
The system SHALL support hot-reload of plugins during development. When a plugin.yaml or plugin source file changes, the system detects the change via file watcher, unloads the old plugin, and reloads the new version without restarting the application.

系统应支持开发期间插件的热重载。当 plugin.yaml 或插件源文件发生变化时，系统通过文件监视器检测变化，卸载旧插件，并重新加载新版本，无需重启应用程序。

#### Scenario: Plugin source file modified — 场景：插件源文件被修改
- **WHEN** a plugin's Python source file is modified while hot-reload is enabled
- **THEN** the system reloads the plugin within 2 seconds and logs the reload event
- **当**热重载启用时插件的 Python 源文件被修改
- **那么**系统在 2 秒内重新加载插件并记录重载事件

#### Scenario: Hot-reload disabled in production — 场景：生产环境中禁用热重载
- **WHEN** the application runs with `HOT_RELOAD=false` (default)
- **THEN** file changes do not trigger reload
- **当**应用程序以 `HOT_RELOAD=false`（默认）运行
- **那么**文件更改不会触发重载

### Requirement: Install-time API surface validation — 需求：安装时 API 表面验证
The system SHALL validate plugin API compatibility at install/load time, beyond 5.5's basic version string check. Validation SHALL verify that the plugin's entry class has the expected method signatures for its declared type (e.g., a Tool Plugin must have `execute` method with correct parameters).

系统应在安装/加载时验证插件 API 兼容性，超越 5.5 的基本版本字符串检查。验证应确认插件的入口类为其声明的类型具有预期的方法签名（例如，工具插件必须具有带有正确参数的 `execute` 方法）。

#### Scenario: Valid plugin passes validation — 场景：有效插件通过验证
- **WHEN** a plugin with correct method signatures is loaded
- **THEN** validation passes and the plugin is registered
- **当**加载具有正确方法签名的插件
- **那么**验证通过并注册插件

#### Scenario: Missing required method detected — 场景：检测到缺少必需方法
- **WHEN** a plugin declares `type: tool` but its class has no `execute` method
- **THEN** validation fails with an error describing the missing method
- **当**插件声明 `type: tool` 但其类没有 `execute` 方法
- **那么**验证失败，返回描述缺失方法的错误

### Requirement: API-type plugin online creation UI — 需求：API 类型插件在线创建 UI
The system SHALL provide a web UI for creating simple plugins (Tool and Trigger types) without writing code. The UI SHALL present a form where users define tool name, description, input/output parameters, and optionally an API endpoint URL. For Trigger type, users define webhook path or cron expression.

系统应提供用于创建简单插件（Tool 和 Trigger 类型）的 Web UI，无需编写代码。UI 应呈现一个表单，用户在其中定义工具名称、描述、输入/输出参数和可选的 API 端点 URL。对于 Trigger 类型，用户定义 webhook 路径或 cron 表达式。

#### Scenario: Create a tool plugin via UI — 场景：通过 UI 创建工具插件
- **WHEN** an administrator fills the plugin creation form with tool name "search-web", description "Web search tool", and API endpoint URL
- **THEN** the system creates a PluginModel with the tool definition and makes it available in the plugin list
- **当**管理员填写插件创建表单，工具名称为"search-web"，描述为"Web search tool"，以及 API 端点 URL
- **那么**系统使用工具定义创建 PluginModel 并使其在插件列表中可用

#### Scenario: Create a webhook trigger via UI — 场景：通过 UI 创建 Webhook 触发器
- **WHEN** an administrator fills the trigger creation form with webhook path "/trigger/my-webhook" and selects a target workflow
- **THEN** the system creates a PluginModel with the trigger definition and registers the webhook endpoint
- **当**管理员填写触发器创建表单，webhook 路径为"/trigger/my-webhook"并选择目标工作流
- **那么**系统使用触发器定义创建 PluginModel 并注册 webhook 端点
