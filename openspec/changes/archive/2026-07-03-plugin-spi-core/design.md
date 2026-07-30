## Context — 背景

Hecate 有 15 个扩展点，但没有统一的插件基础设施。每个服务使用临时注册：`ChannelTypeRegistry.register()`（引擎）、`ToolRegistry`（服务）、`ProviderStrategy.register()`（上下文）、`TaskExecutorRegistry.register()`（调度）、`AuditSecurityPolicy.register()`（审计）。这带来了发现、生命周期和版本兼容性问题。

ADR-016 已决定："所有 SPI 扩展通过单个 `PluginRegistry` 注册。"设计文档 `tool-platform-design.md` 展示了目标 SDK API。本次变更实现了基础。

当前状态：
- `services/evaluation/` 中存在 41 个内置评估器，使用 `Evaluator(ABC)` 基类
- 没有集中式插件注册表
- 没有插件清单格式
- 没有生命周期管理
- Sprint 5 功能（ChannelABC、AuthProviderABC、NotifierABC、EvaluatorABC、i18n SPI）被阻塞

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 为所有 SPI 扩展提供集中式 PluginRegistry
- 定义用于插件元数据的 PluginManifest 数据类
- 定义用于初始化和清理钩子的 PluginLifecycle 协议
- 创建 EvaluatorABC 作为第一个 SPI 类型，重构现有的 41 个评估器以使用它
- 遵循现有的 Hecate 编码模式（ABC、类型注解、`from __future__ import annotations`）

**非目标：**
- YAML 清单文件解析（5.5 Plugin System，Sprint 6）
- 插件沙箱隔离（5.12 MCP Sandbox Security，P4）
- CLI 模板生成器（TP5 Plugin SDK，Sprint 6）
- 热重载能力（TP5，Sprint 6）
- 6 个插件类型定义（5.5，Sprint 6）
- 插件间依赖解析（5.5，Sprint 6）

## Decisions — 决策

### 1. 模块位置：`src/hecate/plugin/`

**决策**：在 `src/hecate/plugin/` 创建新模块，包含 `__init__.py`、`manifest.py`、`registry.py`、`lifecycle.py`。

**理由**：符合设计文档约定（`from hecate.plugin import ...`）。与引擎层清晰分离。遵循 Hecate 模块组织模式。

**备选方案**：
- `src/hecate/core/plugin.py` — 被拒绝：太小，不符合模块约定
- `src/hecate/engine/plugin.py` — 被拒绝：SPI 是平台层，而非引擎层

### 2. PluginRegistry 模式：带类型注册的类级单例

**决策**：`PluginRegistry` 是一个类，包含 `register(plugin_type: str, name: str, plugin: Any) -> None` 和 `get_by_type(plugin_type: str) -> dict[str, Any]`。通过 `threading.Lock` 实现线程安全。

**理由**：匹配现有的 `ChannelTypeRegistry` 和 `ToolRegistry` 模式。简单、可测试、无魔法。

**备选方案**：
- 基于装饰器的自动注册 — 被拒绝：过于隐式，测试难度大
- 模块级函数 — 被拒绝：测试中更难 mock

### 3. PluginManifest：数据类，非 TypedDict 或 Pydantic

**决策**：`@dataclass(frozen=True)`，包含字段：`type`、`name`、`version`、`api_version`、`min_platform_version`、`description`、`permissions`。

**理由**：引擎层避免 Pydantic 依赖。冻结数据类是不可变且可哈希的。匹配 `ports.py` 中的 `SpanContext` 模式。

**备选方案**：
- TypedDict — 被拒绝：无验证，不可哈希
- Pydantic 模型 — 被拒绝：引擎层依赖约束

### 4. PluginLifecycle：Protocol，非 ABC

**决策**：`PluginLifecycle` 作为 `Protocol`，包含 `on_load()` 和 `on_unload()` 方法。对插件是可选的。

**理由**：Protocol 允许鸭子类型——插件不需要显式继承。遵循 Python 类型化最佳实践，用于可选接口。

**备选方案**：
- 含抽象方法的 ABC — 被拒绝：过于严格，不是所有插件都需要生命周期钩子
- 回调注册 — 被拒绝：类型安全性较低

### 5. EvaluatorABC：插件模块中的新接口，重构现有 Evaluator

**决策**：在 `src/hecate/plugin/spi/evaluator.py` 中定义 `EvaluatorABC(ABC)`，包含 `name`、`description`、`evaluate()`。现有的 `Evaluator(ABC)` 成为 `BuiltinEvaluator(EvaluatorABC)` 并继承自它。所有 41 个评估器通过 PluginRegistry 注册。

**理由**：单一概念，一个位置。第三方评估器（7.2-abc 目标）使用相同的接口。无需适配器样板代码。

**备选方案**：
- 包装现有的 Evaluator — 被拒绝：添加适配器层而无益，造成混乱的双重接口

## Risks / Trade-offs — 风险 / 权衡

- **风险**：如果许多插件在启动时注册，PluginRegistry 可能成为瓶颈
  **缓解措施**：注册是 O(n) 但只在启动时发生一次。5.5a 中无运行时热注册。

- **风险**：PluginManifest 的冻结数据类使未来扩展更困难
  **缓解措施**：添加带默认值的可选字段。新字段可以非破坏性地添加。

- **风险**：重构 Evaluator(ABC) 可能破坏现有的 41 个评估器子类
  **缓解措施**：BuiltinEvaluator 继承自 EvaluatorABC，保留现有接口。子类如果直接引用 Evaluator，只需更改导入路径。

- **权衡**：使用 Protocol 表示 PluginLifecycle 意味着在类定义时没有强制约束
  **缓解措施**：PluginRegistry 在注册时检查生命周期方法，如果缺失则记录警告。

## Migration Plan — 迁移计划

1. 创建 `src/hecate/plugin/` 模块，包含 PluginManifest、PluginRegistry、PluginLifecycle
2. 创建 `src/hecate/plugin/spi/evaluator.py`，包含 EvaluatorABC
3. 重构 `services/evaluation/evaluator.py`：Evaluator → BuiltinEvaluator(EvaluatorABC)
4. 更新 `services/evaluation/engine.py`，通过 PluginRegistry 注册所有 41 个评估器
5. 如有需要更新现有评估器的导入（保持向后兼容）
6. 运行 `ruff check src/hecate/ tests/ && ruff format --check src/ tests/ && mypy src/ && python -m pytest tests/ -q`

## Open Questions — 开放问题

- PluginRegistry 是否应具有 `get_all()` 方法还是仅 `get_by_type()`？— 已决定：两者都有，为完整性
- PluginManifest.permissions 应为字符串列表还是更结构化的类型？— 已决定：为简单起见使用字符串列表，以后可扩展
- 是否应添加 `@register_plugin` 装饰器以方便使用？— 已决定：5.5a 中不添加，如果需要则在 5.5 中添加
