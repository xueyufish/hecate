## 1. Plugin Manifest Extension — 插件清单扩展

- [x] 1.1 在 `src/hecate/plugin/manifest.py` 的 `PluginManifest` 中添加 `entry: str`、`permissions: tuple[str, ...]`、`config_schema: dict[str, Any] | None` 字段。更新 `__post_init__` 以将列表类型的 `permissions` 转换为 tuple 以保证不可变性。
- [x] 1.2 在 `src/hecate/core/config.py` 中添加 `PLUGINS_DIR: str = "./plugins"` 设置

## 2. Extended PluginLifecycle — 扩展 PluginLifecycle

- [x] 2.1 在 `src/hecate/plugin/lifecycle.py` 的 `PluginLifecycle` Protocol 中添加 `on_enable`、`on_disable`、`on_config_change` 方法。这三个都是可选的（Protocol 结构类型化——`@runtime_checkable` + `hasattr` 检测）。
- [x] 2.2 更新 `PluginRegistry.register()` 以在实现时调用 `on_load`（现有行为，验证扩展后的 protocol 仍能正常工作）

## 3. Plugin Loader — 插件加载器

- [x] 3.1 创建 `src/hecate/plugin/loader.py`，包含 `PluginLoader` 类：
  - `discover_plugins(plugins_dir: Path) -> list[PluginManifest]` — 扫描目录中的 `plugin.yaml` 文件，将每个解析为 `PluginManifest`
  - `load_plugin(manifest: PluginManifest) -> Any` — 根据 `entry` 前缀分派到 `_load_python()` 或 `_load_mcp()`
  - `_load_python(entry: str) -> Any` — `importlib.import_module(module)` 然后 `getattr(module, cls)()` 实例化
  - `_load_mcp(entry: str) -> Any` — 通过 MCP Client 连接，发现工具，返回包装的代理
  - `_validate_compatibility(manifest: PluginManifest) -> None` — 检查 `api_version` 和 `min_platform_version` 与当前平台版本的兼容性
- [x] 3.2 处理加载器错误：捕获 `ImportError`、`yaml.YAMLError`、`ValueError`，使用 `logger.exception()` 记录日志，不使启动崩溃

## 4. Plugin Configuration Management — 插件配置管理

- [x] 4.1 创建 `src/hecate/plugin/config.py`，包含：
  - `validate_config(config: dict, schema: dict) -> None` — 使用 `jsonschema.validate()` 根据 JSON Schema 验证配置字典，失败时抛出 `ValidationError`
  - `inject_config(plugin_instance: Any, config: dict) -> None` — 将配置注入插件实例（如果实现了 `on_config_change` 则调用，否则设置属性）

## 5. Permission Enforcement — 权限执行

- [x] 5.1 创建 `src/hecate/plugin/permission.py`，包含：
  - `PermissionChecker` 类，接受清单中声明的权限
  - `check_permission(permission: str) -> bool` — 如果权限已声明则返回 True
  - `log_undeclared(permission: str, plugin_name: str) -> None` — 记录未声明权限使用的警告

## 6. PluginModel DB Table — PluginModel 数据库表

- [x] 6.1 创建 `src/hecate/models/plugin.py`，包含 `PluginModel`：
  - `id: UUID`（主键）、`name: str`、`type: str`、`version: str`、`status: str`（枚举：installed/enabled/disabled/error）、`entry: str`、`manifest: dict`（JSON）、`config: dict`（JSON）、`workspace_id: UUID | None`（可空，None = 平台级）、标准时间戳（`created_at`、`updated_at`、`deleted_at`）
  - 继承 `BaseModel`（非 `Base`），遵循现有模型约定
- [x] 6.2 创建 Alembic 迁移 `alembic/versions/xxx_add_plugin_model.py` 以添加 `plugins` 表

## 7. Plugin Service — 插件服务

- [x] 7.1 创建 `src/hecate/services/plugin/service.py`，包含 `PluginService`：
  - `list_plugins(workspace_id: UUID | None) -> list[PluginModel]` — 返回平台级 + 工作空间级插件
  - `get_plugin(plugin_id: UUID) -> PluginModel`
  - `enable_plugin(plugin_id: UUID) -> None` — 将状态更新为 `enabled`，如果实现了则调用 `on_enable`
  - `disable_plugin(plugin_id: UUID) -> None` — 将状态更新为 `disabled`，如果实现了则调用 `on_disable`
  - `update_config(plugin_id: UUID, config: dict) -> None` — 根据 `config_schema` 验证，持久化，调用 `on_config_change`
  - `register_discovered_plugins(plugins_dir: Path) -> int` — 在启动时发现 + 加载 + 持久化插件

## 8. REST API — REST API

- [x] 8.1 创建 `src/hecate/api/management/plugins.py` 路由，前缀为 `/api/plugins`：
  - `GET /` — 列出插件（查询参数：`workspace_id` 可选）
  - `GET /{plugin_id}` — 获取插件详情
  - `POST /{plugin_id}/enable` — 启用插件
  - `POST /{plugin_id}/disable` — 禁用插件
  - `PUT /{plugin_id}/config` — 更新插件配置（请求体：config 字典）
- [x] 8.2 在 `src/hecate/main.py` 中注册 `plugins_router`
- [x] 8.3 在 `src/hecate/api/management/plugins.py` 中添加 Pydantic schemas：`PluginReadSchema`、`PluginConfigUpdateSchema`

## 9. Startup Integration — 启动集成

- [x] 9.1 在 `src/hecate/main.py` 的启动事件中：调用 `PluginService.register_discovered_plugins(settings.PLUGINS_DIR)` 以从插件目录发现并注册插件
- [x] 9.2 记录摘要："Discovered N plugins, M enabled, K errors"（发现 N 个插件，M 个已启用，K 个错误）

## 10. Backend Tests — 后端测试

- [x] 10.1 测试 `PluginLoader.discover_plugins()` — 创建临时 `plugins/` 目录，包含有效和无效的 plugin.yaml 文件，验证发现结果
- [x] 10.2 测试 `PluginLoader._load_python()` — 验证测试插件模块的 importlib 加载，验证不存在模块的错误处理
- [x] 10.3 测试 `_validate_compatibility()` — 验证版本比较接受兼容插件并拒绝不兼容插件
- [x] 10.4 测试 `PluginService.enable_plugin()` / `disable_plugin()` — 验证状态转换和生命周期钩子调用
- [x] 10.5 测试 `PluginService.update_config()` — 验证 JSON Schema 验证接受有效配置并拒绝无效配置
- [x] 10.6 测试双层作用域 — 验证平台级插件对所有工作空间可见，工作空间级插件隔离
- [x] 10.7 测试 REST API 端点 — 通过 `httpx.AsyncClient` 测试列表、获取、启用、禁用、更新配置

## 11. Frontend — Plugin List Page — 前端 — 插件列表页面

- [x] 11.1 创建 `web/src/app/(dashboard)/plugins/page.tsx` — 插件列表表格，包含列：名称、类型、版本、状态徽章（enabled=绿色、disabled=灰色、error=红色）、工作空间作用域指示器
- [x] 11.2 每行插件添加启用/禁用切换按钮 — 调用 `POST /api/plugins/{id}/enable` 或 `POST /api/plugins/{id}/disable`
- [x] 11.3 在 `web/src/components/sidebar.tsx` 中添加指向 `/plugins` 的"插件"链接

## 12. Frontend — Plugin Detail & Config Page — 前端 — 插件详情和配置页面

- [x] 12.1 创建 `web/src/app/(dashboard)/plugins/[id]/page.tsx` — 插件详情页面，显示：清单信息（名称、类型、版本、描述、入口、权限）、连接状态（针对 MCP 插件）、配置表单
- [x] 12.2 实现从 `config_schema` 自动生成配置表单——根据 JSON Schema 类型渲染字段：
  - 带有 `secret: true` 或 `format: password` 的 `string` → 密码输入
  - 带有 `enum` 的 `string` → 下拉选择
  - `string` → 文本输入
  - 带有 `minimum` / `maximum` 的 `number` / `integer` → 带边界的数字输入
  - `boolean` → 切换开关
  - `description` → 字段标签 / 工具提示
- [x] 12.3 配置表单保存按钮 — 调用 `PUT /api/plugins/{id}/config`，显示成功/错误提示

## 13. Verification — 验证

- [x] 13.1 运行 `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 错误
- [x] 13.2 运行 `mypy src/` — 0 错误
- [x] 13.3 运行 `python -m pytest tests/test_plugin/ -q` — 全部通过
- [ ] 13.4 手动验证：创建包含示例 plugin.yaml 的测试 `plugins/` 目录，启动应用，验证插件出现在 UI 中，可以启用/禁用/配置
