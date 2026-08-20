## Why

ADR-029 规定 "any artifact acquired by installation at runtime is never T0"（运行时安装获得的 artifact 无论签名如何永远不进入 in-process 层），但当前 `PluginLoader._load_python()`（`src/hecate/plugin/loader.py`）对任何 `python:module:Class` entry 直接执行 `importlib.import_module()`，没有任何信任检查 —— 运行时安装（或手工投放）一个带 `python:` entry 的 `plugin.yaml` 即可在下次启动时获得 in-process 代码执行权（同地址空间、可访问全部租户数据、event log 与 kernel），正是 ADR-029 所防御的 ClawHub 级供应链风险。此外 5.5b installer 在安装时执行 `uv pip install -r requirements.txt`，为第三方模块铺平了可导入路径。当前 installed-code-plugin 基数约为空，此刻收紧迁移成本近乎为零（roadmap 标注规模 S）。

## What Changes

- **`python:` entry 信任门（load 时强制）**：loader 拒绝非第一方 module 的 in-process 加载。module 为第一方（`hecate` 或 `hecate.*`）时两种部署模式均放行；非第一方时：`SAAS_MODE=true` 直接拒绝；self-hosted 默认拒绝（default-deny），仅当 module 前缀命中 deployer 显式配置的 allowlist 时放行。前缀匹配采用段边界（`mycompany.` 不匹配 `mycompanyevil.*`）。
- **install 时前置拒绝（纵深防御）**：`.hecate-plugin` bundle 安装（`install_plugin_from_bundle`）对 `python:` entry 执行同一策略，违规即在安装时拒绝并提示原因，而不是装完等下次启动才静默跳过。
- **SaaS 禁用 runtime 依赖安装**：`SAAS_MODE=true` 时 installer 跳过 `_install_dependencies()`（`uv pip install`）并记录 WARNING —— SaaS 下非第一方 `python:` entry 本就会被拒，运行时 pip install 只剩供应链风险、无任何收益；self-hosted 保留（其 `python:` entry 已由 loader 门把守）。
- **拒绝语义与现有发现循环一致**：load 时拒绝 = ERROR log（明确指向 T0 policy）+ skip-and-continue + 计入 errors，插件不持久化、不注册。
- **新增配置**：`PLUGIN_PYTHON_ENTRY_ALLOWLIST: list[str] = []`（module 前缀列表，仅 self-hosted 生效；SaaS 模式下忽略该配置，非第一方一律拒绝）。
- **已知权衡（接受）**：SaaS 下允许 `python:hecate.*` entry —— 执行的是 in-repo 第一方代码，插件自身未引入新代码；恶意 manifest 指向平台内部类的 gadget 风险由既有的 type ABC 校验（`validate_api_surface`）backstop，且插件目录不在 `sys.path` 上，第三方无法向 `hecate.` 命名空间注入模块。
- **不受影响**：`mcp://` entry（T2，协议边界）、Agent Plugins 1.0 管线（skills=T4 / http-MCP=T2 / stdio=T1 沙箱，从不 in-process 加载 Python）。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `plugin-system`: 「Entry loading via python: prefix」requirement 增加信任门语义 —— 第一方 module（`hecate` / `hecate.*`）放行，非第一方按部署模式拒绝（SaaS 拒绝；self-hosted default-deny + allowlist）；新增 install 时前置拒绝与 SaaS 禁用 runtime 依赖安装的 requirement。

## Impact

- **代码**：`src/hecate/plugin/loader.py`（`_load_python` / `load_plugin` 增加策略参数与检查）、`src/hecate/services/plugin/service.py`（`register_discovered_plugins` 传入策略；`install_plugin_from_bundle` 前置校验）、`src/hecate/plugin/installer.py`（SaaS 跳过依赖安装）、`src/hecate/core/config.py`（新增 `PLUGIN_PYTHON_ENTRY_ALLOWLIST`）。
- **API/行为**：REST 层无新端点；安装 API 对违规 `python:` entry 返回明确错误。
- **依赖**：无新增第三方依赖。
- **文档**：ADR-029 的 T0 纪律从"实现纪律"落为强制代码；feature catalog 5.5 条目的 T0 tightening 增强注记完成闭环（archive 时更新 `docs/features/feature-catalog.md` 与 `docs/features/roadmap.md`）。
