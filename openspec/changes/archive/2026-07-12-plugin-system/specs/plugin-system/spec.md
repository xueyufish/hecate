## ADDED Requirements — 新增需求

### Requirement: Plugin manifest loading — 需求：插件清单加载
The system SHALL parse `plugin.yaml` files into `PluginManifest` objects. The manifest SHALL support the following fields: `name` (required), `version` (required), `type` (required), `api_version` (required), `min_platform_version` (required), `description`, `entry` (required), `permissions` (list), `config_schema` (JSON Schema object). The loader SHALL validate that all required fields are present and raise a validation error if any are missing.

系统应将 `plugin.yaml` 文件解析为 `PluginManifest` 对象。清单应支持以下字段：`name`（必填）、`version`（必填）、`type`（必填）、`api_version`（必填）、`min_platform_version`（必填）、`description`、`entry`（必填）、`permissions`（列表）、`config_schema`（JSON Schema 对象）。加载器应验证所有必填字段是否存在，如果缺失则抛出验证错误。

#### Scenario: Valid plugin.yaml loaded — 场景：加载有效的 plugin.yaml
- **WHEN** a `plugin.yaml` file with all required fields is loaded
- **THEN** the system returns a `PluginManifest` with all fields populated

- **当**加载包含所有必填字段的 `plugin.yaml` 文件
- **则**系统返回填充了所有字段的 `PluginManifest`

#### Scenario: Missing required field — 场景：缺少必填字段
- **WHEN** a `plugin.yaml` file is missing the `entry` field
- **THEN** the system raises a `ValueError` with a message indicating the missing field

- **当** `plugin.yaml` 文件缺少 `entry` 字段
- **则**系统抛出 `ValueError`，并附带指示缺失字段的消息

#### Scenario: Invalid YAML syntax — 场景：无效的 YAML 语法
- **WHEN** a `plugin.yaml` file contains invalid YAML syntax
- **THEN** the system raises a `yaml.YAMLError` and logs the error

- **当** `plugin.yaml` 文件包含无效的 YAML 语法
- **则**系统抛出 `yaml.YAMLError` 并记录错误

### Requirement: Plugin directory discovery — 需求：插件目录发现
The system SHALL scan a configurable plugins directory (default: `plugins/`) at startup to discover plugin packages. Each plugin package SHALL be a subdirectory containing a `plugin.yaml` file. The system SHALL log discovered plugins and skip directories without a `plugin.yaml`.

系统应在启动时扫描可配置的插件目录（默认：`plugins/`）以发现插件包。每个插件包应是包含 `plugin.yaml` 文件的子目录。系统应记录已发现的插件，并跳过没有 `plugin.yaml` 的目录。

#### Scenario: Discover plugins at startup — 场景：启动时发现插件
- **WHEN** the application starts with `plugins/` containing `plugin-a/plugin.yaml` and `plugin-b/plugin.yaml`
- **THEN** the system discovers both plugins and attempts to load each manifest

- **当**应用程序启动时，`plugins/` 包含 `plugin-a/plugin.yaml` 和 `plugin-b/plugin.yaml`
- **则**系统发现两个插件并尝试加载每个清单

#### Scenario: Skip directories without plugin.yaml — 场景：跳过没有 plugin.yaml 的目录
- **WHEN** `plugins/` contains a `README.md` file but no `plugin.yaml`
- **THEN** the system skips that entry and logs a debug message

- **当** `plugins/` 包含 `README.md` 文件但没有 `plugin.yaml`
- **则**系统跳过该项并记录调试消息

### Requirement: Plugin compatibility validation — 需求：插件兼容性验证
The system SHALL validate `api_version` and `min_platform_version` from the manifest against the current platform version. A plugin whose `min_platform_version` is greater than the current platform version SHALL be rejected with an error message.

系统应根据当前平台版本验证清单中的 `api_version` 和 `min_platform_version`。`min_platform_version` 大于当前平台版本的插件应被拒绝，并附带错误消息。

#### Scenario: Compatible plugin version — 场景：兼容的插件版本
- **WHEN** a plugin declares `min_platform_version: "0.7.0"` and the current platform version is `"0.8.0"`
- **THEN** the system accepts the plugin and proceeds with registration

- **当**插件声明 `min_platform_version: "0.7.0"` 且当前平台版本为 `"0.8.0"`
- **则**系统接受该插件并继续注册

#### Scenario: Incompatible plugin version — 场景：不兼容的插件版本
- **WHEN** a plugin declares `min_platform_version: "0.9.0"` and the current platform version is `"0.8.0"`
- **THEN** the system rejects the plugin, logs an error, and marks it with status `error` in the database

- **当**插件声明 `min_platform_version: "0.9.0"` 且当前平台版本为 `"0.8.0"`
- **则**系统拒绝该插件，记录错误，并在数据库中标记为 `error` 状态

### Requirement: Extended plugin lifecycle — 需求：扩展的插件生命周期
The system SHALL support extended lifecycle hooks beyond the existing `on_load` / `on_unload`: `on_enable` (called when a plugin transitions to enabled state), `on_disable` (called when transitioning to disabled state), and `on_config_change` (called when plugin configuration is updated). Plugins that do not implement these hooks SHALL continue to function without error.

系统应支持扩展的生命周期钩子，超越现有的 `on_load` / `on_unload`：`on_enable`（插件转换为启用状态时调用）、`on_disable`（转换为禁用状态时调用）和 `on_config_change`（插件配置更新时调用）。未实现这些钩子的插件应继续正常运行，不会出错。

#### Scenario: Plugin with all lifecycle hooks — 场景：具有所有生命周期钩子的插件
- **WHEN** a plugin implementing `on_enable`, `on_disable`, and `on_config_change` is enabled
- **THEN** the system calls `on_enable` after updating the plugin status to `enabled` in the database

- **当**实现了 `on_enable`、`on_disable` 和 `on_config_change` 的插件被启用
- **则**系统在将数据库中的插件状态更新为 `enabled` 后调用 `on_enable`

#### Scenario: Plugin without extended hooks — 场景：没有扩展钩子的插件
- **WHEN** a plugin that only implements `on_load` and `on_unload` is enabled
- **THEN** the system updates the status to `enabled` without error and skips the unimplemented hooks

- **当**仅实现了 `on_load` 和 `on_unload` 的插件被启用
- **则**系统将状态更新为 `enabled`，不会出错，并跳过未实现的钩子

#### Scenario: Config change triggers hook — 场景：配置变更触发钩子
- **WHEN** a plugin's configuration is updated via the API and the plugin implements `on_config_change`
- **THEN** the system calls `on_config_change` with the new configuration dictionary after persisting to the database

- **当**通过 API 更新插件配置且插件实现了 `on_config_change`
- **则**系统在持久化到数据库后，使用新的配置字典调用 `on_config_change`

### Requirement: Plugin state persistence — 需求：插件状态持久化
The system SHALL persist plugin state in a `PluginModel` database table with the following attributes: `id` (UUID PK), `name`, `type`, `version`, `status` (enum: `installed`, `enabled`, `disabled`, `error`), `entry`, `manifest` (JSON), `config` (JSON), `workspace_id` (nullable UUID, None for platform-level plugins), and standard timestamps. The database SHALL be the runtime source of truth for plugin state.

系统应将插件状态持久化到 `PluginModel` 数据库表中，包含以下属性：`id`（UUID PK）、`name`、`type`、`version`、`status`（枚举：`installed`、`enabled`、`disabled`、`error`）、`entry`、`manifest`（JSON）、`config`（JSON）、`workspace_id`（可空 UUID，平台级插件为 None）和标准时间戳。数据库应是插件状态的运行时真实来源。

#### Scenario: Register platform-level plugin — 场景：注册平台级插件
- **WHEN** a plugin is discovered from the global `plugins/` directory during startup
- **THEN** the system creates a `PluginModel` with `workspace_id=None` and `status=installed`

- **当**在启动时从全局 `plugins/` 目录发现插件
- **则**系统创建 `PluginModel`，`workspace_id=None`，`status=installed`

#### Scenario: Plugin status transitions — 场景：插件状态转换
- **WHEN** a plugin in `installed` status is enabled via the API
- **THEN** the system updates `status` to `enabled` in the database and calls `on_enable`

- **当**通过 API 启用 `installed` 状态的插件
- **则**系统将数据库中的 `status` 更新为 `enabled` 并调用 `on_enable`

### Requirement: Two-layer plugin scope — 需求：两层插件范围
The system SHALL support two plugin scopes: platform-level (globally available, `workspace_id=None`) and workspace-level (per-workspace, `workspace_id` set). Platform-level plugins SHALL be visible to all workspaces. Workspace-level plugins SHALL only be visible within their workspace.

系统应支持两种插件范围：平台级（全局可用，`workspace_id=None`）和工作空间级（每个工作空间，设置 `workspace_id`）。平台级插件应对所有工作空间可见。工作空间级插件仅在其工作空间内可见。

#### Scenario: Platform-level plugin visible to all workspaces — 场景：平台级插件对所有工作空间可见
- **WHEN** a plugin is registered with `workspace_id=None`
- **THEN** the plugin appears in the plugin list for every workspace

- **当**插件注册时 `workspace_id=None`
- **则**该插件出现在每个工作空间的插件列表中

#### Scenario: Workspace-level plugin isolated — 场景：工作空间级插件隔离
- **WHEN** workspace A has a plugin with `workspace_id=A` and workspace B requests the plugin list
- **THEN** workspace B's plugin list does not include workspace A's plugin

- **当**工作空间 A 有 `workspace_id=A` 的插件，且工作空间 B 请求插件列表
- **则**工作空间 B 的插件列表不包括工作空间 A 的插件

### Requirement: Plugin configuration management — 需求：插件配置管理
The system SHALL support plugin configuration via `config_schema` (JSON Schema) declared in `plugin.yaml`. Configuration values SHALL be stored in the `PluginModel.config` JSON column. The system SHALL validate configuration values against `config_schema` before persisting. The system SHALL inject configuration values into the plugin instance at load time.

系统应通过 `plugin.yaml` 中声明的 `config_schema`（JSON Schema）支持插件配置。配置值应存储在 `PluginModel.config` JSON 列中。系统应在持久化前根据 `config_schema` 验证配置值。系统应在加载时将配置值注入到插件实例中。

#### Scenario: Valid configuration save — 场景：有效的配置保存
- **WHEN** an administrator saves configuration `{"api_key": "xxx", "threshold": 0.8}` for a plugin with a matching `config_schema`
- **THEN** the system validates the config against the schema, persists it to the database, and calls `on_config_change`

- **当**管理员为具有匹配 `config_schema` 的插件保存配置 `{"api_key": "xxx", "threshold": 0.8}`
- **则**系统根据 schema 验证配置，持久化到数据库，并调用 `on_config_change`

#### Scenario: Invalid configuration rejected — 场景：无效的配置被拒绝
- **WHEN** an administrator saves configuration missing a required field defined in `config_schema`
- **THEN** the system rejects the save with a validation error and does not persist

- **当**管理员保存缺少 `config_schema` 中定义的必填字段的配置
- **则**系统拒绝保存并返回验证错误，不进行持久化

#### Scenario: Config injection at load time — 场景：加载时配置注入
- **WHEN** a plugin with `config_schema` and stored config values is loaded
- **THEN** the system injects the stored config values into the plugin instance

- **当**加载具有 `config_schema` 和存储的配置值的插件
- **则**系统将存储的配置值注入到插件实例中

### Requirement: Plugin permission enforcement — 需求：插件权限强制执行
The system SHALL parse permission declarations from `plugin.yaml` (`permissions` field). Plugins SHALL only access resources matching their declared permissions. The system SHALL log warnings when a plugin attempts an undeclared permission.

系统应解析 `plugin.yaml` 中的权限声明（`permissions` 字段）。插件仅应访问与其声明的权限匹配的资源。当插件尝试未声明的权限时，系统应记录警告。

#### Scenario: Plugin with declared permissions — 场景：具有声明权限的插件
- **WHEN** a plugin declares `permissions: ["network:https"]` and makes an HTTPS request
- **THEN** the system allows the operation

- **当**插件声明 `permissions: ["network:https"]` 并发出 HTTPS 请求
- **则**系统允许该操作

#### Scenario: Plugin with undeclared permission — 场景：具有未声明权限的插件
- **WHEN** a plugin declares `permissions: ["network:https"]` but attempts filesystem write
- **THEN** the system logs a warning indicating undeclared permission `filesystem:write`

- **当**插件声明 `permissions: ["network:https"]` 但尝试文件系统写入
- **则**系统记录警告，指示未声明的权限 `filesystem:write`

### Requirement: Entry loading via python: prefix — 需求：通过 python: 前缀加载入口
The system SHALL load plugins with `entry: python:module:Class` format using `importlib.import_module()` to import the module and instantiate the class. The loaded instance SHALL be registered with `PluginRegistry`.

系统应使用 `importlib.import_module()` 加载 `entry: python:module:Class` 格式的插件，导入模块并实例化类。加载的实例应注册到 `PluginRegistry`。

#### Scenario: Load Python plugin — 场景：加载 Python 插件
- **WHEN** a plugin manifest declares `entry: python:my_plugin:MyToolPlugin`
- **THEN** the system imports `my_plugin` module, instantiates `MyToolPlugin`, and registers it with `PluginRegistry`

- **当**插件清单声明 `entry: python:my_plugin:MyToolPlugin`
- **则**系统导入 `my_plugin` 模块，实例化 `MyToolPlugin`，并注册到 `PluginRegistry`

#### Scenario: Invalid Python entry — 场景：无效的 Python 入口
- **WHEN** a plugin manifest declares `entry: python:nonexistent:Class`
- **THEN** the system catches `ImportError`, logs the error, and marks the plugin with status `error`

- **当**插件清单声明 `entry: python:nonexistent:Class`
- **则**系统捕获 `ImportError`，记录错误，并将插件标记为 `error` 状态

### Requirement: Entry loading via mcp:// prefix — 需求：通过 mcp:// 前缀加载入口
The system SHALL load plugins with `entry: mcp://endpoint` format by creating an MCP client connection to the specified endpoint. The MCP server's discovered tools SHALL be registered with `PluginRegistry` as plugin instances.

系统应通过创建到指定端点的 MCP 客户端连接来加载 `entry: mcp://endpoint` 格式的插件。MCP 服务器发现的工具应作为插件实例注册到 `PluginRegistry`。

#### Scenario: Load MCP plugin — 场景：加载 MCP 插件
- **WHEN** a plugin manifest declares `entry: mcp://http://localhost:8080`
- **THEN** the system connects via MCP Client, discovers available tools, and registers them with `PluginRegistry`

- **当**插件清单声明 `entry: mcp://http://localhost:8080`
- **则**系统通过 MCP 客户端连接，发现可用工具，并注册到 `PluginRegistry`

#### Scenario: MCP endpoint unreachable — 场景：MCP 端点不可达
- **WHEN** a plugin manifest declares `entry: mcp://http://localhost:9999` and the endpoint is unreachable
- **THEN** the system catches the connection error, logs it, and marks the plugin with status `error`

- **当**插件清单声明 `entry: mcp://http://localhost:9999` 且端点不可达
- **则**系统捕获连接错误，记录日志，并将插件标记为 `error` 状态

### Requirement: Plugin management REST API — 需求：插件管理 REST API
The system SHALL expose REST API endpoints for plugin management: `GET /api/plugins` (list plugins, filterable by workspace and type), `GET /api/plugins/{id}` (get plugin detail), `POST /api/plugins/{id}/enable` (enable plugin), `POST /api/plugins/{id}/disable` (disable plugin), `PUT /api/plugins/{id}/config` (update plugin configuration).

系统应公开用于插件管理的 REST API 端点：`GET /api/plugins`（列出插件，可按工作空间和类型过滤）、`GET /api/plugins/{id}`（获取插件详情）、`POST /api/plugins/{id}/enable`（启用插件）、`POST /api/plugins/{id}/disable`（禁用插件）、`PUT /api/plugins/{id}/config`（更新插件配置）。

#### Scenario: List plugins — 场景：列出插件
- **WHEN** a client requests `GET /api/plugins`
- **THEN** the system returns a list of all plugins with their status, type, version, and configuration

- **当**客户端请求 `GET /api/plugins`
- **则**系统返回所有插件列表，包含其状态、类型、版本和配置

#### Scenario: Enable plugin — 场景：启用插件
- **WHEN** a client requests `POST /api/plugins/{id}/enable`
- **THEN** the system transitions the plugin to `enabled` status and calls `on_enable`

- **当**客户端请求 `POST /api/plugins/{id}/enable`
- **则**系统将插件转换为 `enabled` 状态并调用 `on_enable`

#### Scenario: Update plugin config — 场景：更新插件配置
- **WHEN** a client requests `PUT /api/plugins/{id}/config` with valid configuration
- **THEN** the system validates against `config_schema`, persists, and calls `on_config_change`

- **当**客户端使用有效配置请求 `PUT /api/plugins/{id}/config`
- **则**系统根据 `config_schema` 验证、持久化，并调用 `on_config_change`

### Requirement: Frontend plugin management page — 需求：前端插件管理页面
The system SHALL provide a web UI plugin management page accessible from the sidebar navigation. The page SHALL display a plugin list with status badges (enabled/disabled/error), type, and version. Clicking a plugin SHALL open a detail page with enable/disable toggle and a configuration form auto-generated from the plugin's `config_schema`.

系统应提供一个可从侧边栏导航访问的 Web UI 插件管理页面。页面应显示插件列表，包含状态徽章（enabled/disabled/error）、类型和版本。点击插件应打开详情页面，包含启用/禁用切换和从插件 `config_schema` 自动生成的配置表单。

#### Scenario: Plugin list page — 场景：插件列表页面
- **WHEN** the administrator navigates to the plugins page
- **THEN** the page displays all plugins with status badges, type, version, and enable/disable toggles

- **当**管理员导航到插件页面
- **则**页面显示所有插件，包含状态徽章、类型、版本和启用/禁用切换

#### Scenario: Config form auto-generation — 场景：配置表单自动生成
- **WHEN** the administrator opens a plugin detail page for a plugin with `config_schema` defining `api_key` (string, secret) and `threshold` (number, 0-1)
- **THEN** the page renders a password input for `api_key` and a number input with bounds 0-1 for `threshold`

- **当**管理员打开插件的详情页面，该插件 `config_schema` 定义了 `api_key`（字符串，密钥）和 `threshold`（数字，0-1）
- **则**页面为 `api_key` 渲染密码输入框，为 `threshold` 渲染范围 0-1 的数字输入框

#### Scenario: MCP endpoint management — 场景：MCP 端点管理
- **WHEN** the administrator opens a plugin detail page for a plugin with `entry: mcp://...`
- **THEN** the page displays the MCP endpoint URL and connection status

- **当**管理员打开具有 `entry: mcp://...` 的插件详情页面
- **则**页面显示 MCP 端点 URL 和连接状态
