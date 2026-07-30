## Why — 为什么

Hecate 有 15 个扩展点（11 核心 + 4 SPI），但没有统一的插件基础设施。每个服务使用临时注册模式：引擎中的 `ChannelTypeRegistry.register()`、服务中的 `ToolRegistry`、上下文中的 `ProviderStrategy.register()`、调度中的 `TaskExecutorRegistry.register()`、审计中的 `AuditSecurityPolicy.register()`。这创建了"N 个不同插件系统"的反模式——没有发现机制、没有生命周期管理、没有版本兼容性、没有依赖解析。

ADR-016 已决定："所有 SPI 扩展通过单个 `PluginRegistry` 注册。"本次变更实现了该决定。Sprint 5 的下游 SPI 功能（ChannelABC、AuthProviderABC、NotifierABC、EvaluatorABC、i18n SPI）在没有集中式插件框架的情况下都被阻塞。

## What Changes — 变更内容

- **新的 `src/hecate/plugin/` 模块** — Plugin SPI Core 框架，包含 PluginManifest、PluginRegistry 和 PluginLifecycle
- **新的 `EvaluatorABC`** — 正式的评估接口，现有的 41 个评估器将通过 PluginRegistry 实现
- **重构 `Evaluator(ABC)`** — 现有的评估器基类成为 `BuiltinEvaluator(EvaluatorABC)`，注册为内置评估器插件类型
- **无破坏性变更** — 现有的评估器子类继续不变地工作

## Capabilities — 能力

### 新能力

- `plugin-manifest`：PluginManifest 数据类 — type、name、version、api_version、min_platform_version、permissions、description
- `plugin-registry`：PluginRegistry — register、unregister、get_by_type、get_by_name、list_all，具有线程安全存储
- `plugin-lifecycle`：PluginLifecycle 协议 — 用于插件初始化和清理的 on_load、on_unload 钩子
- `evaluator-abc`：EvaluatorABC — 通过 PluginRegistry 注册的评估插件接口（name、description、evaluate）

### 修改的能力

- 无——这是新增能力，对现有功能无需求变更

## Impact — 影响

- **新模块**：`src/hecate/plugin/`（4 个文件：`__init__.py`、`manifest.py`、`registry.py`、`lifecycle.py`）
- **修改**：`src/hecate/services/evaluation/evaluator.py` — Evaluator(ABC) 成为 BuiltinEvaluator(EvaluatorABC)
- **修改**：`src/hecate/services/evaluation/engine.py` — 通过 PluginRegistry 注册 41 个评估器
- **测试**：PluginRegistry、PluginManifest、PluginLifecycle、EvaluatorABC 注册的新测试文件
- **依赖**：无——纯 Python，不需要外部包
- **下游**：解除 Sprint 5 功能的阻塞：ChannelABC（11.1-abc）、AuthProviderABC（10.3-abc）、NotifierABC（8.6-abc）、i18n SPI（15.1）
