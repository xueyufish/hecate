## 1. Plugin Bundle Format — 插件 Bundle 格式

- [x] 1.1 Create `src/hecate/plugin/packaging.py` with:
  - `create_bundle(plugin_dir: Path, output_path: Path | None = None) -> Path` — validate plugin.yaml exists, ZIP the directory contents, output `.hecate-plugin` file
  - `extract_bundle(bundle_path: Path, target_dir: Path) -> Path` — extract ZIP to target_dir, return extracted plugin directory path
  - `validate_bundle(bundle_path: Path) -> bool` — check file is valid ZIP containing plugin.yaml
- [x] 1.1 创建 `src/hecate/plugin/packaging.py`，包含：
  - `create_bundle(plugin_dir: Path, output_path: Path | None = None) -> Path` — 验证 plugin.yaml 存在，ZIP 目录内容，输出 `.hecate-plugin` 文件
  - `extract_bundle(bundle_path: Path, target_dir: Path) -> Path` — 将 ZIP 解压到 target_dir，返回解压后的插件目录路径
  - `validate_bundle(bundle_path: Path) -> bool` — 检查文件是否为包含 plugin.yaml 的有效 ZIP

## 2. Plugin Installer — 插件安装器

- [x] 2.1 Create `src/hecate/plugin/installer.py` with:
  - `install_plugin(bundle_path: Path, plugins_dir: Path) -> str` — extract bundle, install deps via `uv pip install -r requirements.txt`, return plugin name
  - `uninstall_plugin(plugin_name: str, plugins_dir: Path) -> bool` — delete plugin directory, return True if deleted
  - `_install_dependencies(plugin_dir: Path) -> None` — run `uv pip install -r requirements.txt` if requirements.txt exists
- [x] 2.1 创建 `src/hecate/plugin/installer.py`，包含：
  - `install_plugin(bundle_path: Path, plugins_dir: Path) -> str` — 解压 bundle，通过 `uv pip install -r requirements.txt` 安装依赖，返回插件名称
  - `uninstall_plugin(plugin_name: str, plugins_dir: Path) -> bool` — 删除插件目录，如果已删除则返回 True
  - `_install_dependencies(plugin_dir: Path) -> None` — 如果 requirements.txt 存在则运行 `uv pip install -r requirements.txt`

## 3. CLI Extensions — CLI 扩展

- [x] 3.1 Add `package` subcommand to `src/hecate/plugin/cli.py` — `hecate plugin package <dir>` calls `create_bundle()`
- [x] 3.2 Add `install` subcommand to `src/hecate/plugin/cli.py` — `hecate plugin install <file.hecate-plugin>` calls `install_plugin()`
- [x] 3.3 Add `uninstall` subcommand to `src/hecate/plugin/cli.py` — `hecate plugin uninstall <name>` calls `uninstall_plugin()`
- [x] 3.1 在 `src/hecate/plugin/cli.py` 中添加 `package` 子命令 — `hecate plugin package <dir>` 调用 `create_bundle()`
- [x] 3.2 在 `src/hecate/plugin/cli.py` 中添加 `install` 子命令 — `hecate plugin install <file.hecate-plugin>` 调用 `install_plugin()`
- [x] 3.3 在 `src/hecate/plugin/cli.py` 中添加 `uninstall` 子命令 — `hecate plugin uninstall <name>` 调用 `uninstall_plugin()`

## 4. Service Layer — 服务层

- [x] 4.1 Add `install_plugin(bundle_path: str) -> PluginModel` to `PluginService` — calls installer, creates/updates PluginModel, loads plugin via PluginLoader
- [x] 4.2 Add `uninstall_plugin(plugin_id: uuid.UUID) -> None` to `PluginService` — calls installer to delete directory, deletes PluginModel record. Reject if plugin is built-in (workspace_id is None AND status is not "installed" via plugin.yaml)
- [x] 4.1 在 `PluginService` 中添加 `install_plugin(bundle_path: str) -> PluginModel` — 调用安装器，创建/更新 PluginModel，通过 PluginLoader 加载插件
- [x] 4.2 在 `PluginService` 中添加 `uninstall_plugin(plugin_id: uuid.UUID) -> None` — 调用安装器删除目录，删除 PluginModel 记录。如果插件是内置的则拒绝（workspace_id 为 None 且状态不是通过 plugin.yaml "installed"）

## 5. REST API

- [x] 5.1 Add `POST /api/plugins/upload` endpoint to `src/hecate/api/management/plugins.py` — accepts multipart file upload (.hecate-plugin), saves to temp, calls `PluginService.install_plugin()`, returns PluginReadSchema
- [x] 5.2 Add `DELETE /api/plugins/{id}` endpoint — calls `PluginService.uninstall_plugin()`, returns 200. Reject built-in plugins with 403
- [x] 5.1 在 `src/hecate/api/management/plugins.py` 中添加 `POST /api/plugins/upload` 端点 — 接受 multipart 文件上传 (.hecate-plugin)，保存到临时位置，调用 `PluginService.install_plugin()`，返回 PluginReadSchema
- [x] 5.2 添加 `DELETE /api/plugins/{id}` 端点 — 调用 `PluginService.uninstall_plugin()`，返回 200。对内置插件返回 403 拒绝

## 6. Frontend — Upload UI — 前端 — 上传 UI

- [x] 6.1 Add "Upload Plugin" button to `web/src/app/(dashboard)/plugins/page.tsx` — opens hidden file input for `.hecate-plugin` files, POSTs to `/api/plugins/upload`, refreshes list on success
- [x] 6.2 Show upload progress / error toast
- [x] 6.1 在 `web/src/app/(dashboard)/plugins/page.tsx` 中添加"上传插件"按钮 — 为 `.hecate-plugin` 文件打开隐藏的文件输入，POST 到 `/api/plugins/upload`，成功后刷新列表
- [x] 6.2 显示上传进度/错误提示

## 7. Frontend — Uninstall UI — 前端 — 卸载 UI

- [x] 7.1 Add "Uninstall" button to `web/src/app/(dashboard)/plugins/[id]/page.tsx` — calls `DELETE /api/plugins/{id}`, redirects to plugin list on success
- [x] 7.2 Hide "Uninstall" button for built-in plugins (workspace_id is null AND entry starts with "python:hecate.")
- [x] 7.1 在 `web/src/app/(dashboard)/plugins/[id]/page.tsx` 中添加"卸载"按钮 — 调用 `DELETE /api/plugins/{id}`，成功后重定向到插件列表
- [x] 7.2 对内置插件隐藏"卸载"按钮（workspace_id 为 null 且入口以 "python:hecate." 开头）

## 8. Backend Tests — 后端测试

- [x] 8.1 Test `create_bundle()` — create temp plugin dir with plugin.yaml, package it, verify ZIP contents
- [x] 8.2 Test `create_bundle()` rejects dir without plugin.yaml
- [x] 8.3 Test `extract_bundle()` — extract a valid bundle, verify plugin.yaml and source files present
- [x] 8.4 Test `install_plugin()` — install a bundle to temp plugins dir, verify PluginModel created
- [x] 8.5 Test `uninstall_plugin()` — uninstall an installed plugin, verify directory deleted and PluginModel removed
- [x] 8.6 Test install upgrade — install v1, then install v2 with same name, verify version updated
- [x] 8.7 Test `POST /api/plugins/upload` via httpx AsyncClient
- [x] 8.8 Test `DELETE /api/plugins/{id}` — verify uninstall via API, verify 403 for built-in plugins
- [x] 8.1 测试 `create_bundle()` — 用 plugin.yaml 创建临时插件目录，打包，验证 ZIP 内容
- [x] 8.2 测试 `create_bundle()` 拒绝没有 plugin.yaml 的目录
- [x] 8.3 测试 `extract_bundle()` — 解压有效的 bundle，验证 plugin.yaml 和源文件存在
- [x] 8.4 测试 `install_plugin()` — 将 bundle 安装到临时插件目录，验证 PluginModel 已创建
- [x] 8.5 测试 `uninstall_plugin()` — 卸载已安装的插件，验证目录已删除且 PluginModel 已移除
- [x] 8.6 测试安装升级 — 安装 v1，然后用相同名称安装 v2，验证版本已更新
- [x] 8.7 通过 httpx AsyncClient 测试 `POST /api/plugins/upload`
- [x] 8.8 测试 `DELETE /api/plugins/{id}` — 通过 API 验证卸载，验证内置插件返回 403

## 9. Verification — 验证

- [x] 9.1 Run `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 errors
- [x] 9.2 Run `mypy src/` — 0 errors
- [x] 9.3 Run `python -m pytest tests/test_plugin/ -q` — all pass
- [x] 9.4 Manual verification: `hecate plugin init test-tool --type tool`, `hecate plugin package ./test-tool`, `hecate plugin install test-tool.hecate-plugin`, verify appears in UI, then uninstall
- [x] 9.1 运行 `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 错误
- [x] 9.2 运行 `mypy src/` — 0 错误
- [x] 9.3 运行 `python -m pytest tests/test_plugin/ -q` — 全部通过
- [x] 9.4 手动验证：`hecate plugin init test-tool --type tool`、`hecate plugin package ./test-tool`、`hecate plugin install test-tool.hecate-plugin`，验证出现在 UI 中，然后卸载
