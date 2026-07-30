## MODIFIED Requirements — 修改后的需求

### Requirement: Evaluation input/output types — 需求：评估输入/输出类型
The system SHALL define typed dataclasses for evaluation I/O: `EvalInput` (query, retrieved_contexts, generated_answer, expected_answer, tool_calls, conversation_history, system_prompt, agent_id, session_id, metadata), `EvalOutput` (scores list, metadata, duration_ms). The new fields `conversation_history`, `system_prompt`, `agent_id`, and `session_id` SHALL be optional with default values for backward compatibility.

系统应为评估 I/O 定义类型化数据类：`EvalInput`（query、retrieved_contexts、generated_answer、expected_answer、tool_calls、conversation_history、system_prompt、agent_id、session_id、metadata）、`EvalOutput`（scores list、metadata、duration_ms）。新字段 `conversation_history`、`system_prompt`、`agent_id` 和 `session_id` 应为可选的，带有默认值以实现向后兼容。

#### Scenario: RAG evaluation input — 场景：RAG 评估输入
- **WHEN** evaluating a RAG pipeline result
- **THEN** `EvalInput` SHALL contain at minimum: query (str), retrieved_contexts (list[str]), generated_answer (str), and optionally expected_answer (str | None)

- **当**评估 RAG 管线结果时
- **则** `EvalInput` 至少应包含：query（str）、retrieved_contexts（list[str]）、generated_answer（str）和可选的 expected_answer（str | None）

#### Scenario: Agent evaluation input — 场景：Agent 评估输入
- **WHEN** evaluating an agent response
- **THEN** `EvalInput` SHALL contain at minimum: query (str), generated_answer (str), and optionally expected_answer (str | None) and tool_calls (list[dict] | None)

- **当**评估 Agent 响应时
- **则** `EvalInput` 至少应包含：query（str）、generated_answer（str）和可选的 expected_answer（str | None）及 tool_calls（list[dict] | None）

#### Scenario: Multi-turn evaluation input — 场景：多轮评估输入
- **WHEN** evaluating a multi-turn conversation
- **THEN** `EvalInput` SHALL contain `conversation_history` (list[dict]) with the full conversation turns for multi-turn evaluators

- **当**评估多轮对话时
- **则** `EvalInput` 应包含 `conversation_history`（list[dict]），包含多轮评估器的完整对话轮次

#### Scenario: Instruction following evaluation input — 场景：指令遵循评估输入
- **WHEN** evaluating instruction compliance
- **THEN** `EvalInput` SHALL contain `system_prompt` (str | None) for the evaluator to compare against generated output

- **当**评估指令合规性时
- **则** `EvalInput` 应包含 `system_prompt`（str | None），供评估器与生成的输出进行比较

### Requirement: Evaluation execution engine — 需求：评估执行引擎
The system SHALL provide an `EvaluationEngine` in `services/evaluation/engine.py` that accepts a list of `Evaluator` instances and an `EvaluationDataset`, runs all evaluators against all items, and produces an `EvaluationRunResult` with aggregated scores. The engine SHALL run deterministic evaluators in parallel (via asyncio.gather) before LLM-judge evaluators to optimize throughput.

系统应在 `services/evaluation/engine.py` 中提供 `EvaluationEngine`，接受 `Evaluator` 实例列表和 `EvaluationDataset`，对所有项目运行所有评估器，并生成带有聚合得分的 `EvaluationRunResult`。引擎应在 LLM 评判评估器之前并行运行确定性评估器（通过 asyncio.gather）以优化吞吐量。

#### Scenario: Batch evaluation execution — 场景：批量评估执行
- **WHEN** `EvaluationEngine.run(evaluators, dataset)` is called
- **THEN** the engine SHALL execute each evaluator against each dataset item, collect all scores, compute per-metric averages, and return an `EvaluationRunResult`

- **当**调用 `EvaluationEngine.run(evaluators, dataset)`
- **则**引擎应对每个数据集项执行每个评估器，收集所有得分，计算每个指标的平均值，并返回 `EvaluationRunResult`

#### Scenario: Evaluator failure isolation — 场景：评估器失败隔离
- **WHEN** an individual evaluator raises an exception during execution
- **THEN** the engine SHALL catch the exception, log it, record a failed score with reasoning="Evaluator error: {message}", and continue with remaining evaluators/items

- **当**单个评估器在执行期间抛出异常
- **则**引擎应捕获异常、记录日志、记录失败得分（reasoning="Evaluator error: {message}"），并继续处理其余评估器/项目

#### Scenario: Deterministic evaluators run in parallel — 场景：确定性评估器并行运行
- **WHEN** the engine executes a mix of deterministic and LLM-judge evaluators
- **THEN** deterministic evaluators (source="deterministic") SHALL be executed concurrently via asyncio.gather, while LLM-judge evaluators SHALL be executed sequentially to respect rate limits

- **当**引擎执行混合的确定性和 LLM 评判评估器
- **则**确定性评估器（source="deterministic"）应通过 asyncio.gather 并发执行，而 LLM 评判评估器应顺序执行以遵守速率限制

#### Scenario: Tag-filtered evaluation run — 场景：标签过滤的评估运行
- **WHEN** `EvaluationEngine.run(evaluators, dataset, tags=["smoke"])` is called
- **THEN** only items with matching tags SHALL be evaluated

- **当**调用 `EvaluationEngine.run(evaluators, dataset, tags=["smoke"])`
- **则**仅评估具有匹配标签的项目
