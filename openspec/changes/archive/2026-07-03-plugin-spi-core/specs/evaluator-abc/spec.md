## ADDED Requirements — 新增需求

### Requirement: EvaluatorABC 接口 — EvaluatorABC interface
系统应在 `src/hecate/plugin/spi/evaluator.py` 中定义一个 `EvaluatorABC` 抽象基类，评估插件必须实现。接口必须包含：
- `name: str`（属性）— 指标的简短标识符
- `description: str`（属性）— 人类可读的描述
- `evaluate(input: EvalInput) -> EvalOutput`（方法）— 异步评估逻辑

#### Scenario: 创建评估器插件 — Create evaluator plugin
- **WHEN** 开发者创建一个继承自 EvaluatorABC 的类
- **THEN** 该类必须实现 name、description 和 evaluate()

#### Scenario: 尝试实例化抽象评估器 — Attempt to instantiate abstract evaluator
- **WHEN** 开发者尝试直接实例化 EvaluatorABC
- **THEN** 抛出 TypeError（抽象类不能实例化）

### Requirement: BuiltinEvaluator 适配器 — BuiltinEvaluator adapter
系统应将 `services/evaluation/evaluator.py` 中现有的 `Evaluator(ABC)` 重构为 `BuiltinEvaluator(EvaluatorABC)`。所有现有的 41 个评估器子类必须无需修改继续工作。

#### Scenario: 现有评估器子类工作正常 — Existing evaluator subclass works
- **WHEN** 实例化现有评估器子类（例如 FaithfulnessEvaluator）
- **THEN** 其工作方式与重构前完全相同

#### Scenario: BuiltinEvaluator 继承自 EvaluatorABC — BuiltinEvaluator inherits from EvaluatorABC
- **WHEN** 开发者检查 BuiltinEvaluator
- **THEN** 它是 EvaluatorABC 的子类并满足评估器接口

### Requirement: 通过 PluginRegistry 注册评估器 — Evaluator registration via PluginRegistry
系统应在启动时将所有 41 个内置评估器注册到 PluginRegistry，使用 type="evaluator"。

#### Scenario: 所有评估器已注册 — All evaluators registered
- **WHEN** 应用程序启动
- **THEN** 所有 41 个内置评估器在 PluginRegistry 中以 type="evaluator" 注册

#### Scenario: 按名称查找评估器 — Lookup evaluator by name
- **WHEN** 开发者调用 `registry.get_by_name("evaluator", "faithfulness")`
- **THEN** 返回 FaithfulnessEvaluator 实例

#### Scenario: 列出所有评估器 — List all evaluators
- **WHEN** 开发者调用 `registry.get_by_type("evaluator")`
- **THEN** 返回以名称为键的所有 41 个评估器的字典
