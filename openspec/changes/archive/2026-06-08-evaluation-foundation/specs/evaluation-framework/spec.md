## ADDED Requirements — 新增的需求

### Requirement: Evaluator abstract base class — 需求：Evaluator 抽象基类
系统应在 `services/evaluation/evaluator.py` 中提供一个 `Evaluator` 抽象基类，带有一个所有评估器必须实现的异步 `evaluate(input: EvalInput) -> EvalOutput` 方法。

#### Scenario: Custom evaluator implementation — 场景：自定义评估器实现
- **WHEN — 当** 开发人员创建继承自 `Evaluator` 的类
- **THEN — 则** 该类必须实现 `evaluate()` 异步方法并声明其 `name: str` 和 `description: str` 属性

#### Scenario: Evaluator with custom LLM config — 场景：带自定义 LLM 配置的评估器
- **WHEN — 当** 使用指定模型、温度和 api_base 的 `llm_config` 参数实例化评估器
- **THEN — 则** 评估器应使用该 LLM 配置进行所有评估调用，如果未指定则回退到默认模型

### Requirement: Structured score output — 需求：结构化的分数输出
系统应在 `services/evaluation/types.py` 中定义一个 `Score` dataclass，包含字段：`metric_name: str`、`value: float`（0.0–1.0）、`reasoning: str | None`、`source: str`（其中之一："llm_judge"、"deterministic"、"human"）。

#### Scenario: Score value range validation — 场景：Score 值范围验证
- **WHEN — 当** 创建值在 0.0–1.0 范围之外的 Score
- **THEN — 则** 系统应引发 `ValueError`

### Requirement: Evaluation input/output types — 需求：评估输入/输出类型
系统应为评估 I/O 定义类型化的 dataclass：`EvalInput`（query、retrieved_contexts、generated_answer、expected_answer）、`EvalOutput`（scores 列表、metadata、duration_ms）。

#### Scenario: RAG evaluation input — 场景：RAG 评估输入
- **WHEN — 当** 评估 RAG 管道结果时
- **THEN — 则** `EvalInput` 应至少包含：query（str）、retrieved_contexts（list[str]）、generated_answer（str），以及可选的 expected_answer（str | None）

#### Scenario: Agent evaluation input — 场景：智能体评估输入
- **WHEN — 当** 评估智能体响应时
- **THEN — 则** `EvalInput` 应至少包含：query（str）、generated_answer（str），以及可选的 expected_answer（str | None）和 tool_calls（list[dict] | None）

### Requirement: Evaluation execution engine — 需求：评估执行引擎
系统应在 `services/evaluation/engine.py` 中提供一个 `EvaluationEngine`，接受 `Evaluator` 实例列表和 `EvaluationDataset`，对所有项运行所有评估器，并生成带聚合分数的 `EvaluationRunResult`。

#### Scenario: Batch evaluation execution — 场景：批处理评估执行
- **WHEN — 当** 调用 `EvaluationEngine.run(evaluators, dataset)`
- **THEN — 则** 引擎应对每个数据集项执行每个评估器，收集所有分数，计算每个指标平均值，并返回 `EvaluationRunResult`

#### Scenario: Evaluator failure isolation — 场景：评估器故障隔离
- **WHEN — 当** 单个评估器在执行期间引发异常
- **THEN — 则** 引擎应捕获异常、记录日志、记录带 reasoning="Evaluator error: {message}" 的失败 Score，并继续处理剩余的评估器/项
