## Why — 为什么

Hecate's Plugin System (5.5 ✅) loads plugins from a local `plugins/` directory, and TP5 ✅ provides type ABCs + `hecate plugin init` CLI for scaffolding. But there is no way to package a plugin into a distributable bundle, install it from a file, or uninstall it. Developers must manually copy plugin directories. This blocks plugin distribution beyond local development — no upload/install workflow, no version upgrade, no enterprise deployment of plugins across environments.

Hecate 的插件系统（5.5 ✅）从本地 `plugins/` 目录加载插件，TP5 ✅ 提供类型 ABC + `hecate plugin init` CLI 用于脚手架。但无法将插件打包成可分发的 bundle、从文件安装或卸载。开发者必须手动复制插件目录。这阻碍了插件在本地开发之外的分布 — 没有上传/安装工作流，没有版本升级，无法跨环境进行企业级插件部署。

## What Changes — 变更内容

- **`.hecate-plugin` bundle format**: ZIP archive containing `plugin.yaml` + Python source + `requirements.txt`. Extension of the existing plugin directory structure into a distributable package.
- **`hecate plugin package` CLI**: Packages a plugin directory into a `.hecate-plugin` ZIP bundle. Validates that `plugin.yaml` exists and is well-formed before packaging.
- **`hecate plugin install` CLI**: Installs a `.hecate-plugin` bundle — extracts to `plugins/` directory, installs Python dependencies via `uv pip install -r requirements.txt`, registers in PluginModel DB.
- **`hecate plugin uninstall` CLI**: Removes a plugin — deletes the plugin directory, unregisters from PluginModel DB, unloads from PluginRegistry.
- **Upload/install UI**: Plugin management page gains an "Upload Plugin" button that accepts `.hecate-plugin` files. Backend extracts, installs dependencies, and registers the plugin.
- **Uninstall UI**: Plugin detail page gains an "Uninstall" button.
- **Version management**: Installing a newer version of an existing plugin overwrites the old directory and updates the PluginModel version field.
- **Dependency installation**: After extracting the bundle, `uv pip install -r requirements.txt` is run to install the plugin's Python dependencies.

- **`.hecate-plugin` bundle 格式**：包含 `plugin.yaml` + Python 源码 + `requirements.txt` 的 ZIP 归档。将现有插件目录结构扩展为可分发的包。
- **`hecate plugin package` CLI**：将插件目录打包为 `.hecate-plugin` ZIP bundle。在打包前验证 `plugin.yaml` 存在且格式正确。
- **`hecate plugin install` CLI**：安装 `.hecate-plugin` bundle — 解压到 `plugins/` 目录，通过 `uv pip install -r requirements.txt` 安装 Python 依赖，在 PluginModel DB 中注册。
- **`hecate plugin uninstall` CLI**：移除插件 — 删除插件目录，从 PluginModel DB 注销，从 PluginRegistry 卸载。
- **上传/安装 UI**：插件管理页面增加"上传插件"按钮，接受 `.hecate-plugin` 文件。后端解压、安装依赖并注册插件。
- **卸载 UI**：插件详情页面增加"卸载"按钮。
- **版本管理**：安装已存在插件的新版本会覆盖旧目录并更新 PluginModel 版本字段。
- **依赖安装**：解压 bundle 后，运行 `uv pip install -r requirements.txt` 安装插件的 Python 依赖。

## Capabilities — 能力

### New Capabilities — 新能力

- `plugin-packaging`: .hecate-plugin ZIP bundle format, packaging CLI, install/uninstall CLI, upload/install/uninstall REST API, upload/uninstall UI, version upgrade workflow, post-install dependency handling

### Modified Capabilities — 修改的能力

- `plugin-system`: PluginService gains `install_plugin(bundle_path)` and `uninstall_plugin(plugin_id)` methods; PluginModel may need an `installed_version` field for tracking the installed bundle version

## Impact — 影响

- **New files**:
  - `src/hecate/plugin/packaging.py` — bundle creation, extraction, validation
  - `src/hecate/plugin/installer.py` — install/uninstall logic with dependency handling
- **Modified files**:
  - `src/hecate/plugin/cli.py` — add `package`, `install`, `uninstall` subcommands
  - `src/hecate/services/plugin/service.py` — add `install_plugin()`, `uninstall_plugin()`
  - `src/hecate/api/management/plugins.py` — add `POST /upload`, `DELETE /{id}` endpoints
  - `web/src/app/(dashboard)/plugins/page.tsx` — add "Upload Plugin" button
  - `web/src/app/(dashboard)/plugins/[id]/page.tsx` — add "Uninstall" button

- **新文件**：
  - `src/hecate/plugin/packaging.py` — bundle 创建、解压、验证
  - `src/hecate/plugin/installer.py` — 安装/卸载逻辑及依赖处理
- **修改的文件**：
  - `src/hecate/plugin/cli.py` — 添加 `package`、`install`、`uninstall` 子命令
  - `src/hecate/services/plugin/service.py` — 添加 `install_plugin()`、`uninstall_plugin()`
  - `src/hecate/api/management/plugins.py` — 添加 `POST /upload`、`DELETE /{id}` 端点
  - `web/src/app/(dashboard)/plugins/page.tsx` — 添加"上传插件"按钮
  - `web/src/app/(dashboard)/plugins/[id]/page.tsx` — 添加"卸载"按钮
