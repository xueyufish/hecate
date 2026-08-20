# Tasks: t0-runtime-plugin-tightening

## 1. 策略基础（config + policy 对象）

- [x] 1.1 `core/config.py` 新增 `PLUGIN_PYTHON_ENTRY_ALLOWLIST: list[str] = []`（注释说明仅 self-hosted 生效、SaaS 模式忽略）
- [x] 1.2 `loader.py` 新增 frozen dataclass `PythonEntryPolicy`（`saas_mode` / `allowed_prefixes` + `from_settings`），及纯函数 `check_python_entry(entry, policy) -> str | None`：第一方判定（`hecate` 精确 / `hecate.` 前缀）、SaaS 一律拒绝、self-hosted default-deny + 段边界前缀匹配；拒绝原因文案含补救指引（加 allowlist / 移除插件）

## 2. Loader 信任门（load 时权威门）

- [x] 2.1 `_load_python` / `load_plugin` 增加 policy 必填参数，在 `importlib.import_module` 之前调用 `check_python_entry`；拒绝时按 skip-and-continue 语义返回 None 并记 ERROR（指名 T0 policy / ADR-029）
- [x] 2.2 `tests/test_plugin/test_loader.py` 新增用例：第一方 `hecate.plugins.x` 两模式放行；`hecate` 精确匹配放行；SaaS 拒绝非第一方（即使配了 allowlist）；self-hosted 空 allowlist 拒绝；allowlist 前缀命中放行；段边界（`mycompany.` 不放行 `mycompanyevil.*`）；拒绝后 `load_plugin` 返回 None 且不触发 import；既有用例改传 policy

## 3. Service 接线（发现循环 + install 前置拒绝）

- [x] 3.1 `PluginService.register_discovered_plugins` 从 Settings 构造 `PythonEntryPolicy` 并传入 `load_plugin`；确认 errors 计数与"不持久化"语义
- [x] 3.2 `PluginService.install_plugin_from_bundle` 在 `_persist_plugin` 前对 `python:` entry 调用 `check_python_entry`；拒绝时删除刚解压的插件目录（回滚）并抛 `ValueError`（信息含补救指引）
- [x] 3.3 `tests/test_services/`（或对应 service 测试文件）新增用例：违规 bundle 安装失败 + 目录被清理；第一方 bundle 安装成功；发现循环中被拒插件计入 errors 且无 PluginModel 行

## 4. Installer SaaS 依赖安装门

- [x] 4.1 `installer._install_dependencies` 开头检查 `SAAS_MODE`：为 true 跳过 `uv pip install` 并记 WARNING，安装流程继续；self-hosted 行为不变
- [x] 4.2 测试：SaaS 模式带 `requirements.txt` 的 bundle 安装成功且依赖安装被跳过（可用 monkeypatch 断言 subprocess 未被调用）；self-hosted 正常调用

## 5. 验证与文档

- [x] 5.1 验证 `hot_reload.py` 路径重发现经由 service（即天然过门），无独立 import 路径；如有旁路则补接线
  - 验证完成：`PluginHotReloader` 仅依赖用户提供的 `reload_callback`（grep 确认无人实例化），不直接调用 `load_plugin`。任何未来接线都会经由 `PluginService.register_discovered_plugins` 走同一条 T0 门。无代码变更。
- [x] 5.2 全量本地 CI：`ruff check src/hecate/ tests/` + `ruff format --check src/ tests/` + `mypy src/` + `python -m pytest tests/ -q` 全绿
  - 验证结果：ruff check 全通过；ruff format 全通过；mypy 全通过（539 source files，0 errors）；pytest **3585 passed, 10 skipped, 9 deselected, 1 xfailed** in 1145.93s
- [x] 5.3 归档时同步 feature catalog：`docs/features/feature-catalog.md` 5.5 条目 T0 tightening 增强注记标记完成、`docs/features/roadmap.md` 5.5 (enh) 条目勾销（随 `/opsx-archive` 流程执行）
  - 同步完成（archive 流程内）：feature-catalog 5.5 条目增强注记改写为"已交付"、P3 完成数 83→84、标题状态条加入 5.5 (enh) T0 Tightening ship 注释；roadmap 5.5 (enh) 条目标 ✅ 并加 ship 元数据
