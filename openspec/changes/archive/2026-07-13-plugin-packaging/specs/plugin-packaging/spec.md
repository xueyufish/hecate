## ADDED Requirements — 新增需求

### Requirement: Plugin bundle format — 需求：插件 Bundle 格式
The system SHALL support a `.hecate-plugin` bundle format — a ZIP archive containing a valid plugin directory structure (`plugin.yaml` + Python source files + optional `requirements.txt`). The packaging function SHALL validate that `plugin.yaml` exists and contains required fields before creating the bundle.

系统应支持 `.hecate-plugin` bundle 格式 — 包含有效插件目录结构（`plugin.yaml` + Python 源文件 + 可选的 `requirements.txt`）的 ZIP 归档。打包函数应在创建 bundle 前验证 `plugin.yaml` 存在且包含必填字段。

#### Scenario: Package a valid plugin directory — 场景：打包有效的插件目录
- **WHEN** a developer runs `hecate plugin package ./my-plugin`
- **THEN** the system creates `my-plugin.hecate-plugin` ZIP file containing all files from the directory
- **当**开发者运行 `hecate plugin package ./my-plugin`
- **那么**系统创建包含目录中所有文件的 `my-plugin.hecate-plugin` ZIP 文件

#### Scenario: Reject directory without plugin.yaml — 场景：拒绝没有 plugin.yaml 的目录
- **WHEN** a developer runs `hecate plugin package ./not-a-plugin` and no `plugin.yaml` exists
- **THEN** the system rejects with an error message
- **当**开发者运行 `hecate plugin package ./not-a-plugin` 且不存在 `plugin.yaml`
- **那么**系统拒绝并返回错误消息

#### Scenario: Bundle contains requirements.txt — 场景：Bundle 包含 requirements.txt
- **WHEN** a plugin directory contains `requirements.txt`
- **THEN** the bundle includes it and the installer will install dependencies after extraction
- **当**插件目录包含 `requirements.txt`
- **那么**bundle 会包含它，安装器会在解压后安装依赖

### Requirement: Plugin install from bundle — 需求：从 Bundle 安装插件
The system SHALL support installing a `.hecate-plugin` bundle. Installation SHALL: extract the ZIP to the `plugins/` directory, install Python dependencies from `requirements.txt` via `uv pip install`, create or update a PluginModel record, and load the plugin via the existing PluginLoader.

系统应支持安装 `.hecate-plugin` bundle。安装应：将 ZIP 解压到 `plugins/` 目录，通过 `uv pip install` 从 `requirements.txt` 安装 Python 依赖，创建或更新 PluginModel 记录，并通过现有的 PluginLoader 加载插件。

#### Scenario: Install new plugin — 场景：安装新插件
- **WHEN** an administrator runs `hecate plugin install my-plugin.hecate-plugin`
- **THEN** the system extracts the bundle to `plugins/my-plugin/`, installs dependencies, creates a PluginModel record, and the plugin appears in the plugin list
- **当**管理员运行 `hecate plugin install my-plugin.hecate-plugin`
- **那么**系统将 bundle 解压到 `plugins/my-plugin/`，安装依赖，创建 PluginModel 记录，插件出现在插件列表中

#### Scenario: Install upgrades existing plugin — 场景：安装升级现有插件
- **WHEN** an administrator installs a bundle whose plugin name already exists with an older version
- **THEN** the system overwrites the existing directory, updates the PluginModel version field, and reloads the plugin
- **当**管理员安装的 bundle 的插件名称已存在且版本更旧
- **那么**系统覆盖现有目录，更新 PluginModel 版本字段，并重新加载插件

#### Scenario: Install invalid bundle — 场景：安装无效 Bundle
- **WHEN** an administrator attempts to install a corrupted or non-ZIP file
- **THEN** the system rejects with an error and does not modify the plugins directory
- **当**管理员尝试安装损坏或非 ZIP 文件
- **那么**系统拒绝并返回错误，不修改插件目录

### Requirement: Plugin uninstall — 需求：插件卸载
The system SHALL support uninstalling a plugin. Uninstall SHALL: delete the plugin directory from `plugins/`, delete the PluginModel record, and unregister from PluginRegistry.

系统应支持卸载插件。卸载应：从 `plugins/` 删除插件目录，删除 PluginModel 记录，并从 PluginRegistry 注销。

#### Scenario: Uninstall installed plugin — 场景：卸载已安装的插件
- **WHEN** an administrator runs `hecate plugin uninstall my-plugin`
- **THEN** the system removes `plugins/my-plugin/`, deletes the PluginModel record, and the plugin no longer appears in the plugin list
- **当**管理员运行 `hecate plugin uninstall my-plugin`
- **那么**系统移除 `plugins/my-plugin/`，删除 PluginModel 记录，插件不再出现在插件列表中

#### Scenario: Uninstall non-existent plugin — 场景：卸载不存在的插件
- **WHEN** an administrator runs `hecate plugin uninstall nonexistent`
- **THEN** the system reports that the plugin is not installed
- **当**管理员运行 `hecate plugin uninstall nonexistent`
- **那么**系统报告插件未安装

### Requirement: Upload plugin via REST API — 需求：通过 REST API 上传插件
The system SHALL expose a `POST /api/plugins/upload` endpoint that accepts a `.hecate-plugin` file upload. The backend SHALL extract, install dependencies, and register the plugin.

系统应暴露 `POST /api/plugins/upload` 端点，接受 `.hecate-plugin` 文件上传。后端应解压、安装依赖并注册插件。

#### Scenario: Upload valid bundle — 场景：上传有效 Bundle
- **WHEN** a client uploads a valid `.hecate-plugin` file to `POST /api/plugins/upload`
- **THEN** the system installs the plugin and returns the PluginReadSchema
- **当**客户端将有效的 `.hecate-plugin` 文件上传到 `POST /api/plugins/upload`
- **那么**系统安装插件并返回 PluginReadSchema

#### Scenario: Upload invalid file — 场景：上传无效文件
- **WHEN** a client uploads a non-ZIP file
- **THEN** the system returns a 400 error
- **当**客户端上传非 ZIP 文件
- **那么**系统返回 400 错误

### Requirement: Delete plugin via REST API — 需求：通过 REST API 删除插件
The system SHALL expose a `DELETE /api/plugins/{id}` endpoint that uninstalls a plugin.

系统应暴露 `DELETE /api/plugins/{id}` 端点用于卸载插件。

#### Scenario: Delete installed plugin — 场景：删除已安装的插件
- **WHEN** a client sends `DELETE /api/plugins/{id}`
- **THEN** the system uninstalls the plugin and returns 200
- **当**客户端发送 `DELETE /api/plugins/{id}`
- **那么**系统卸载插件并返回 200

#### Scenario: Delete built-in plugin rejected — 场景：删除内置插件被拒绝
- **WHEN** a client sends `DELETE /api/plugins/{id}` for a built-in plugin
- **THEN** the system returns 403 with "Built-in plugins cannot be uninstalled"
- **当**客户端为内置插件发送 `DELETE /api/plugins/{id}`
- **那么**系统返回 403，提示"内置插件无法卸载"

### Requirement: Upload plugin UI — 需求：上传插件 UI
The system SHALL provide an "Upload Plugin" button on the plugin management page that opens a file picker for `.hecate-plugin` files. On successful upload, the plugin list refreshes.

系统应在插件管理页面提供"上传插件"按钮，打开 `.hecate-plugin` 文件的文件选择器。上传成功后，插件列表刷新。

#### Scenario: Upload via UI — 场景：通过 UI 上传
- **WHEN** an administrator clicks "Upload Plugin" and selects a `.hecate-plugin` file
- **THEN** the system uploads the file, installs the plugin, and the new plugin appears in the list
- **当**管理员点击"上传插件"并选择 `.hecate-plugin` 文件
- **那么**系统上传文件、安装插件，新插件出现在列表中

### Requirement: Uninstall plugin UI — 需求：卸载插件 UI
The system SHALL provide an "Uninstall" button on the plugin detail page. Built-in plugins SHALL NOT show the uninstall button.

系统应在插件详情页面提供"卸载"按钮。内置插件不应显示卸载按钮。

#### Scenario: Uninstall via UI — 场景：通过 UI 卸载
- **WHEN** an administrator clicks "Uninstall" on a third-party plugin detail page
- **THEN** the system uninstalls the plugin and redirects to the plugin list
- **当**管理员在第三方插件详情页面点击"卸载"
- **那么**系统卸载插件并重定向到插件列表

#### Scenario: Built-in plugin has no uninstall button — 场景：内置插件没有卸载按钮
- **WHEN** an administrator views a built-in plugin detail page
- **THEN** the "Uninstall" button is not displayed
- **当**管理员查看内置插件详情页面
- **那么**"卸载"按钮不显示
