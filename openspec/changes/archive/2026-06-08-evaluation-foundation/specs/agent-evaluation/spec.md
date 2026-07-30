## ADDED Requirements — 新增的需求

### Requirement: Correctness evaluator — 需求：Correctness 评估器
系统应提供一个 `CorrectnessEvaluator`，使用 LLM-as-Judge 比较生成答案与预期答案。它应评估事实准确性和完整性。

#### Scenario: Compare against ground truth — 场景：与基本事实比较
- **WHEN — 当** 提供了生成答案和预期答案
- **THEN — 则** 评估器应使用 LLM-as-Judge 返回带 `metric_name="correctness"` 的 Score，`value` 反映事实准确性（1.0 = 完全正确，0.0 = 完全错误）

#### Scenario: No expected answer provided — 场景：未提供预期答案
- **WHEN — 当** 请求正确性评估但未提供预期答案
- **THEN — 则** 评估器应返回带 `value=-1.0` 和 `reasoning="No expected answer provided"` 的 Score

### Requirement: Relevancy evaluator — 需求：Relevancy 评估器
系统应提供一个 `RelevancyEvaluator`，使用 LLM-as-Judge 衡量智能体响应对用户查询的响应程度。

#### Scenario: Evaluate response relevance — 场景：评估响应相关性
- **WHEN — 当** 根据用户查询评估智能体响应
- **THEN — 则** 评估器应返回带 `metric_name="relevancy"` 的 Score，指示响应是否直接回答问题

#### Scenario: Off-topic response — 场景：离题响应
- **WHEN — 当** 智能体响应与用户查询无关
- **THEN — 则** 评估器应返回接近 0.0 的 `value`，并附带解释不匹配的 reasoning

### Requirement: Completeness evaluator — 需求：Completeness 评估器
系统应提供一个 `CompletenessEvaluator`，使用 LLM-as-Judge 衡量智能体响应是否覆盖用户查询的所有方面。

#### Scenario: Evaluate multi-aspect query coverage — 场景：评估多方面查询覆盖
- **WHEN — 当** 用户查询有多个方面，响应涵盖了一些但不是全部
- **THEN — 则** 评估器应返回带 `metric_name="completeness"` 和与涵盖方面比例成正比的 `value` 的 Score

#### Scenario: Fully complete response — 场景：完全完整的响应
- **WHEN — 当** 智能体响应涵盖用户查询的所有方面
- **THEN — 则** 评估器应返回 `value=1.0`
