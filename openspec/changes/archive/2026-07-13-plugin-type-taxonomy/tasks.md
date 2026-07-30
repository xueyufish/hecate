## 1. New Plugin Type ABCs — 新插件类型 ABC

- [x] 1.1 Create `src/hecate/plugin/types/tool.py` — `ToolPluginABC` with `name`, `description` properties and async `execute(params: dict) -> dict` method
- [x] 1.2 Create `src/hecate/plugin/types/extension.py` — `ExtensionPluginABC` with optional callbacks `on_pre_llm`, `on_post_llm`, `on_pre_tool`, `on_post_tool` (Google ADK BasePlugin pattern)
- [x] 1.3 Create `src/hecate/plugin/types/trigger.py` — `TriggerPluginABC` with `trigger_type` (webhook/schedule/event), async `on_webhook`, `on_schedule`, `on_event` methods
- [x] 1.4 Create `src/hecate/plugin/types/model.py` — `ModelPluginABC` with async `invoke(messages, config) -> dict` and `embed(text) -> list[float]` methods
- [x] 1.5 Create `src/hecate/plugin/types/__init__.py` — type registry mapping type strings to ABCs, re-export all 4 new ABCs + 4 existing ABCs (ChannelABC, EvaluatorABC, AuthProviderABC, SecretProviderABC)
- [x] 1.1 创建 `src/hecate/plugin/types/tool.py` — `ToolPluginABC`，包含 `name`、`description` 属性和 async `execute(params: dict) -> dict` 方法
- [x] 1.2 创建 `src/hecate/plugin/types/extension.py` — `ExtensionPluginABC`，包含可选回调 `on_pre_llm`、`on_post_llm`、`on_pre_tool`、`on_post_tool`（Google ADK BasePlugin 模式）
- [x] 1.3 创建 `src/hecate/plugin/types/trigger.py` — `TriggerPluginABC`，包含 `trigger_type`（webhook/schedule/event）和 async `on_webhook`、`on_schedule`、`on_event` 方法
- [x] 1.4 创建 `src/hecate/plugin/types/model.py` — `ModelPluginABC`，包含 async `invoke(messages, config) -> dict` 和 `embed(text) -> list[float]` 方法
- [x] 1.5 创建 `src/hecate/plugin/types/__init__.py` — 类型注册表，将类型字符串映射到 ABC，重新导出所有 4 个新 ABC + 4 个现有 ABC（ChannelABC、EvaluatorABC、AuthProviderABC、SecretProviderABC）

## 2. SDK Module — SDK 模块

- [x] 2.1 Create `src/hecate/plugin/sdk.py` — `hecate.plugin` SDK module: re-export all 8 type ABCs from single import path, `PluginContext` class (config access + permission checking), `register()` helper function
- [x] 2.2 Update `src/hecate/plugin/__init__.py` to re-export SDK symbols for `from hecate.plugin import ToolPluginABC`
- [x] 2.1 创建 `src/hecate/plugin/sdk.py` — `hecate.plugin` SDK 模块：从单一导入路径重新导出所有 8 种类型 ABC，`PluginContext` 类（配置访问 + 权限检查），`register()` 辅助函数
- [x] 2.2 更新 `src/hecate/plugin/__init__.py` 以重新导出 SDK 符号，支持 `from hecate.plugin import ToolPluginABC`

## 3. Type-Aware Loader — 类型感知加载器

- [x] 3.1 Update `src/hecate/plugin/loader.py` — `load_plugin()` validates that loaded entry class implements the correct ABC for its declared `type` field. Add `_validate_type(manifest, plugin_instance)` that checks `isinstance` against the type registry
- [x] 3.2 Add type validation errors to existing error handling (reject plugins with wrong ABC, log clearly)
- [x] 3.1 更新 `src/hecate/plugin/loader.py` — `load_plugin()` 验证加载的入口类为其声明的 `type` 字段实现了正确的 ABC。添加 `_validate_type(manifest, plugin_instance)`，针对类型注册表检查 `isinstance`
- [x] 3.2 将类型验证错误添加到现有错误处理中（拒绝具有错误 ABC 的插件，清晰记录日志）

## 4. Install-Time API Validation — 安装时 API 验证

- [x] 4.1 Create `src/hecate/plugin/validation.py` — `validate_api_surface(manifest, plugin_instance) -> list[str]` that checks method signatures match the expected ABC contract (e.g., Tool Plugin must have `execute` method). Returns list of validation errors (empty = valid)
- [x] 4.2 Integrate `validate_api_surface` into loader — call after `_validate_type`, reject plugin if errors found
- [x] 4.1 创建 `src/hecate/plugin/validation.py` — `validate_api_surface(manifest, plugin_instance) -> list[str]`，检查方法签名是否与预期的 ABC 契约匹配（例如，工具插件必须有 `execute` 方法）。返回验证错误列表（空 = 有效）
- [x] 4.2 将 `validate_api_surface` 集成到加载器中 — 在 `_validate_type` 之后调用，如果发现错误则拒绝插件

## 5. CLI Template Generator — CLI 模板生成器

- [x] 5.1 Create `src/hecate/plugin/cli.py` — `hecate plugin init <name> --type <type>` command using click or argparse. Accepts all 8 types. Scaffolds: `plugin.yaml`, `__init__.py` (with correct ABC subclass), `test_<name>.py`
- [x] 5.2 Register CLI command in `src/hecate/main.py` or as standalone `hecate` CLI entry point
- [x] 5.3 Add `HOT_RELOAD: bool = False` setting to `src/hecate/core/config.py`
- [x] 5.1 创建 `src/hecate/plugin/cli.py` — 使用 click 或 argparse 的 `hecate plugin init <name> --type <type>` 命令。接受所有 8 种类型。脚手架：`plugin.yaml`、`__init__.py`（带有正确的 ABC 子类）、`test_<name>.py`
- [x] 5.2 在 `src/hecate/main.py` 或作为独立的 `hecate` CLI 入口点注册 CLI 命令
- [x] 5.3 在 `src/hecate/core/config.py` 中添加 `HOT_RELOAD: bool = False` 设置

## 6. Hot-Reload — 热重载

- [x] 6.1 Add `watchdog` to `[dev]` optional dependencies in `pyproject.toml`
- [x] 6.2 Create `src/hecate/plugin/hot_reload.py` — `PluginHotReloader` class using `watchdog.Observer` to watch plugins directory. On file change: unload old plugin, reload new, update PluginRegistry. Only active when `settings.HOT_RELOAD=True`
- [x] 6.3 Integrate hot-reloader into startup — start observer if `HOT_RELOAD=True`, log "Hot-reload enabled"
- [x] 6.1 将 `watchdog` 添加到 `pyproject.toml` 中的 `[dev]` 可选依赖
- [x] 6.2 创建 `src/hecate/plugin/hot_reload.py` — 使用 `watchdog.Observer` 监视插件目录的 `PluginHotReloader` 类。文件变化时：卸载旧插件，重新加载新插件，更新 PluginRegistry。仅在 `settings.HOT_RELOAD=True` 时激活
- [x] 6.3 将热重载集成到启动中 — 如果 `HOT_RELOAD=True` 则启动观察器，记录"热重载已启用"日志

## 7. Extension Plugin Bridge to Guardrail Hooks — 扩展插件桥接到 Guardrail 钩子

- [x] 7.1 Create bridge logic in `src/hecate/plugin/types/extension.py` — `ExtensionPluginAdapter` class that wraps an `ExtensionPluginABC` instance and exposes it as `PreLLMHook` / `PostLLMHook` / `PreToolHook` / `PostToolHook` to the existing engine guardrail system
- [x] 7.2 Register adapter with engine's guardrail config when Extension Plugin is enabled
- [x] 7.1 在 `src/hecate/plugin/types/extension.py` 中创建桥接逻辑 — `ExtensionPluginAdapter` 类，包装 `ExtensionPluginABC` 实例并将其作为 `PreLLMHook`/`PostLLMHook`/`PreToolHook`/`PostToolHook` 暴露给现有引擎护栏系统
- [x] 7.2 当扩展插件启用时，在引擎的护栏配置中注册适配器

## 8. Backend Tests — 后端测试

- [x] 8.1 Test `ToolPluginABC` — verify subclass creates valid tool, verify abstract methods enforced
- [x] 8.2 Test `ExtensionPluginABC` — verify partial callbacks work (only `on_pre_tool` implemented → others skipped)
- [x] 8.3 Test `ExtensionPluginAdapter` bridge — verify it correctly delegates to existing Hook system
- [x] 8.4 Test `TriggerPluginABC` — verify webhook and schedule trigger types
- [x] 8.5 Test `ModelPluginABC` — verify invoke and embed methods
- [x] 8.6 Test type-aware loader — verify correct ABC validated for each type, verify rejection on mismatch
- [x] 8.7 Test `validate_api_surface` — verify method signature checking catches missing methods
- [x] 8.8 Test `hecate plugin init` CLI — verify scaffolding for each of 8 types, verify invalid type rejected
- [x] 8.9 Test hot-reload — verify file change triggers reload (mock file watcher)
- [x] 8.10 Test SDK module imports — verify `from hecate.plugin import ToolPluginABC` works
- [x] 8.1 测试 `ToolPluginABC` — 验证子类创建有效工具，验证抽象方法强制执行
- [x] 8.2 测试 `ExtensionPluginABC` — 验证部分回调工作（仅实现 `on_pre_tool` → 其他跳过）
- [x] 8.3 测试 `ExtensionPluginAdapter` 桥接 — 验证正确委托给现有 Hook 系统
- [x] 8.4 测试 `TriggerPluginABC` — 验证 webhook 和调度触发器类型
- [x] 8.5 测试 `ModelPluginABC` — 验证 invoke 和 embed 方法
- [x] 8.6 测试类型感知加载器 — 验证每种类型都检查正确的 ABC，验证不匹配时拒绝
- [x] 8.7 测试 `validate_api_surface` — 验证方法签名检查能捕获缺失的方法
- [x] 8.8 测试 `hecate plugin init` CLI — 验证每种类型的脚手架，验证无效类型被拒绝
- [x] 8.9 测试热重载 — 验证文件更改触发重载（模拟文件监视器）
- [x] 8.10 测试 SDK 模块导入 — 验证 `from hecate.plugin import ToolPluginABC` 工作

## 9. Frontend — API-Type Plugin Creation UI — 前端 — API 类型插件创建 UI

- [x] 9.1 Create `web/src/app/(dashboard)/plugins/create/page.tsx` — plugin creation form with type selector (Tool/Trigger), parameter definition table, API endpoint URL field (for Tool), webhook path + cron expression fields (for Trigger)
- [x] 9.2 Add "Create Plugin" button to `web/src/app/(dashboard)/plugins/page.tsx` — links to create page
- [x] 9.3 Create backend API endpoint `POST /api/plugins/create` — accepts plugin definition, creates PluginModel with generated manifest
- [x] 9.1 创建 `web/src/app/(dashboard)/plugins/create/page.tsx` — 插件创建表单，包含类型选择器（Tool/Trigger）、参数定义表、API 端点 URL 字段（用于 Tool）、webhook 路径 + cron 表达式字段（用于 Trigger）
- [x] 9.2 在 `web/src/app/(dashboard)/plugins/page.tsx` 中添加"创建插件"按钮 — 链接到创建页面
- [x] 9.3 创建后端 API 端点 `POST /api/plugins/create` — 接受插件定义，使用生成的清单创建 PluginModel

## 10. Verification — 验证

- [x] 10.1 Run `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 errors
- [x] 10.2 Run `mypy src/` — 0 errors
- [x] 10.3 Run `python -m pytest tests/test_plugin/ -q` — all pass (80/80)
- [ ] 10.4 Manual verification: run `hecate plugin init test-tool --type tool`, verify scaffold, load it, verify it appears in UI
- [x] 10.1 运行 `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 错误
- [x] 10.2 运行 `mypy src/` — 0 错误
- [x] 10.3 运行 `python -m pytest tests/test_plugin/ -q` — 全部通过 (80/80)
- [ ] 10.4 手动验证：运行 `hecate plugin init test-tool --type tool`，验证脚手架，加载它，验证它出现在 UI 中
