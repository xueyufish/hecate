# Design: t0-runtime-plugin-tightening (5.5 enh)

## Context

plugin 体系中 in-process 代码执行只有一个咽喉点：`src/hecate/plugin/loader.py` 的 `_load_python()`（`importlib.import_module` 在整个 plugin 目录下仅此一处），所有加载路径（启动发现 `main.py` → `PluginService.register_discovered_plugins()` → `load_plugin()`；bundle 安装；hot-reload 重发现）最终都汇入它。当前它对 `python:module:Class` 无任何信任检查。

配置侧已有先例可复用：`Settings.SAAS_MODE`（5.5c stdio skip-and-warn 已用）、`Settings.PLUGINS_DIR`、config-allowlist 承接权限的 `PLATFORM_PLUGIN_INSTALLERS` 命名风格。`loader.py` 目前是纯函数模块，不导入 Settings —— 策略需要显式注入。

5.5c Agent Plugins 管线（skills=T4 / http-MCP=T2 / stdio=T1 沙箱）从不 in-process 加载 Python，不受本变更影响；`mcp://` entry 走协议边界，同样不受影响。本变更打击面 = `.hecate-plugin` bundle / 手工投放目录 + `python:` entry。

## Goals / Non-Goals

**Goals:**

- 单一策略函数实现信任门，load 时与 install 时共用，杜绝两处逻辑漂移
- fail-closed：默认拒绝，前缀段边界匹配防混淆（`mycompany.` 不放行 `mycompanyevil.*`）
- SaaS 模式禁用 runtime `pip install`（供应链铺路机制在该模式下无收益、纯风险）
- 拒绝语义与现有发现循环一致（skip-and-continue + ERROR log + errors 计数）

**Non-Goals:**

- 5.5c 管线的信任分发（已按 ADR-029 落地，不含 in-process 路径）
- 签名 / 内容扫描（5.13a 已 ship，5.13 在 P5）
- 非 `hecate.` entry 的隔离运行时（T1 容器 / T3 WASM 承载 python entry —— ADR-029 Phase B 的 "isolation inversion"，本变更只做拒绝、不做改造）
- allowlist 的 per-plugin 粒度（目录级 / hash 级对 S 规模是过度设计；前缀级已覆盖 ADR-029 T0 情形 (c) —— deployer 自有 source-tree 扩展）
- `PluginModel` schema 变更（无新字段；被拒插件不落库，无迁移）

## Decisions

### D1: 策略以值对象注入 loader，loader 保持纯净

新增 frozen dataclass `PythonEntryPolicy`（字段 `saas_mode: bool`、`allowed_prefixes: tuple[str, ...]`，含类方法 `from_settings(settings)`），定义在 `loader.py`（或其旁）。`load_plugin(manifest, policy)` / `_load_python(entry, policy)` 增加必填 policy 参数；`PluginService.register_discovered_plugins` 在入口处从 Settings 构造一次并传入。

- 备选：loader 内部直接读全局 Settings —— 否决：破坏 loader 纯函数风格，隐式全局依赖伤及可测性，且 `_load_python` 是安全关键路径，显式传参让测试无法"忘记"策略维度。
- 备选：模块级全局 policy + `configure()` —— 否决：同上，且引入初始化顺序问题。

签名改为必填（而非带默认值的可选参数）：调用方只有两处（service 发现循环、install 路径），全部显式传参，编译期（mypy）即暴露遗漏，不存在"忘了传策略 → 门失效"的静默路径。

### D2: 第一方判定 = `hecate` 精确匹配或 `hecate.` 前缀

`module == "hecate" or module.startswith("hecate.")`。依据：plugin 目录不上 `sys.path`，第三方无法向 `hecate.` 命名空间投放模块；`hecate` 发行版已占据这些模块，runtime `pip install` 无法覆写。因此 `hecate.` 前缀 ⟺ in-repo 代码，module 前缀即溯源代理 —— 这同时消解了"目录扫描无法判定谁投放的文件"的不可判定问题（proposal Q1）。

allowlist 前缀匹配规则：`module == prefix.rstrip(".") or module.startswith(prefix if prefix.endswith(".") else prefix + ".")`，即只认模块名段边界。SaaS 模式下 allowlist 整体忽略（非第一方一律拒绝）。

### D3: 单一策略函数，load / install 双执行点

策略核心为一个纯函数 `check_python_entry(entry: str, policy: PythonEntryPolicy) -> str | None`（返回 None = 放行；返回 str = 拒绝原因，含补救指引），放在 `loader.py`。两个执行点：

1. **load 时（权威门）**：`_load_python` 在 `importlib.import_module` 之前调用。手工投放目录绕过一切安装 API，但绕不过启动发现 —— 此门不可绕过。
2. **install 时（前置拒绝 + 回滚）**：`PluginService.install_plugin_from_bundle` 在 bundle 解压后、`_persist_plugin` 前对 manifest 的 `python:` entry 调用同一函数；拒绝时删除刚解压的插件目录并抛 `ValueError`（API 层映射为明确错误信息），管理员获得即时反馈而非装完等下次启动静默跳过。

拒绝日志措辞统一指向 "T0 policy (ADR-029)" 并给出补救（self-hosted: 将 module 前缀加入 `PLUGIN_PYTHON_ENTRY_ALLOWLIST`；否则移除插件），避免运维面对"插件消失"无从下手。

### D4: SaaS 禁跑 runtime 依赖安装，self-hosted 保留

`installer._install_dependencies` 开头检查 `SAAS_MODE`：为 true 时跳过并记 WARNING，安装流程本身继续（`mcp://` entry 的 bundle 在 SaaS 完全合法，不能因携带无关紧要的 `requirements.txt` 而整体拒绝）。self-hosted 保留 —— 合法 deployer 扩展需要装依赖，且其可导入性已被 D2 的 loader 门约束。

- 备选：SaaS 下 bundle 带 `requirements.txt` 即整体拒绝安装 —— 否决：惩罚了合法的 MCP-only bundle，跳过 + WARNING 已消除供应链风险本身（不执行 pip 即无风险），比例失当。
- 注：installer 模块级函数不便注入 policy，此处直接读 Settings 是可接受的例外（与 5.5c `agent_plugins.py` 读 `SAAS_MODE` 的先例一致）。

### D5: 拒绝语义 = skip-and-continue

`load_plugin` 捕获门拒绝后返回 `None`（与现有失败路径一致）→ 发现循环 `errors += 1`、continue、不持久化。不引入新的 PluginModel 状态（如 `rejected`）：被拒插件不是"装了但禁用"，而是"从未进入系统"，落库反而制造需要清理的状态。

## Risks / Trade-offs

- [SaaS 运营方确有第三方 in-process 插件需求] → 设计上不支持（ADR-029：fork / deployer CI 扩展）；错误信息明确指引该路径，不留模糊期望。
- [存量 self-hosted 部署升级后第三方 `python:` 插件被拒] → 现状 installed-code-plugin 基数约空（roadmap 判断）；迁移 = 将前缀加入 `PLUGIN_PYTHON_ENTRY_ALLOWLIST`，启动日志附补救指引；无数据迁移，回滚 = 回退版本。
- [`python:hecate.*` 指向平台内部类的 gadget] → 接受的权衡（proposal 已记录）：type ABC 校验（`validate_api_surface`）为 backstop，插件自身无法引入新代码；且 in-repo 目前无走 `plugin.yaml` 的第一方插件，实际暴露面近零。
- [双执行点未来漂移] → 单一 `check_python_entry` 函数 + 双点共用同一 policy 对象；测试断言两处行为一致。
- [hot-reload 路径绕过] → 现查 `hot_reload.py` 不直接调用 `load_plugin`（grep 证实调用点仅 service），重发现经由 service 同一门；tasks 中保留一项显式验证。

## Migration Plan

1. 发布含本变更的版本（默认 `PLUGIN_PYTHON_ENTRY_ALLOWLIST=[]`，即默认收紧）。
2. 升级后观察启动日志：被拒插件出现指名 T0 policy 的 ERROR；需要保留的 deployer 自有扩展按日志指引加前缀进 allowlist 并重启。
3. 回滚：直接回退版本。无 DB 迁移、无 schema 变更、无状态残留（被拒插件从未持久化）。
