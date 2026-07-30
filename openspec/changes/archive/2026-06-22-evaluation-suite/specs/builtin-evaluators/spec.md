## ADDED Requirements — 新增需求

### Requirement: Four-layer evaluator taxonomy — 需求：四层评估器分类法
The system SHALL organize all built-in evaluators into four categories: Result Layer (output quality), Process Layer (tool/reasoning correctness), Interaction Layer (multi-turn coherence), and Generic/Programmatic Layer (deterministic, LLM-judge, code execution, safety). Each evaluator SHALL declare its category via a `category` class attribute.

系统应将所有内置评估器组织为四个类别：结果层（输出质量）、过程层（工具/推理正确性）、交互层（多轮连贯性）和通用/编程层（确定性、LLM-judge、代码执行、安全）。每个评估器应通过 `category` 类属性声明其类别。

#### Scenario: List evaluators by category — 场景：按类别列出评估器
- **WHEN** `GET /api/evaluation/evaluators?category=result` is called
- **THEN** only evaluators with `category="result"` are returned

- **当**调用 `GET /api/evaluation/evaluators?category=result`
- **则**仅返回 `category="result"` 的评估器

#### Scenario: List all evaluators with categories — 场景：列出所有带类别的评估器
- **WHEN** `GET /api/evaluation/evaluators` is called without a category filter
- **THEN** all registered evaluators are returned grouped by category, each with name, description, category, source type (deterministic/llm_judge), and required input fields

- **当**调用 `GET /api/evaluation/evaluators` 时不带类别过滤器
- **则**返回所有已注册评估器，按类别分组，每个包含名称、描述、类别、来源类型（deterministic/llm_judge）和必需输入字段

### Requirement: Deterministic format evaluators — 需求：确定性格式评估器
The system SHALL provide 6 deterministic format evaluators that do not require LLM calls: `exact_match`, `contains`, `contains_any`, `regex_match`, `is_json`, `format_check`. Each SHALL produce a Score with `source="deterministic"` and execute in sub-millisecond time.

系统应提供 6 个不需要 LLM 调用的确定性格式评估器：`exact_match`、`contains`、`contains_any`、`regex_match`、`is_json`、`format_check`。每个应生成 `source="deterministic"` 的 Score，并在亚毫秒时间内执行。

#### Scenario: Exact match evaluator — 场景：精确匹配评估器
- **WHEN** the `exact_match` evaluator is called with `generated_answer="Paris"` and `expected_answer="Paris"`
- **THEN** it SHALL return a Score with `value=1.0` and `source="deterministic"`

- **当**`exact_match` 评估器被调用，`generated_answer="Paris"` 且 `expected_answer="Paris"`
- **则**应返回 `value=1.0` 和 `source="deterministic"` 的 Score

#### Scenario: Contains evaluator with substring — 场景：包含子字符串评估器
- **WHEN** the `contains` evaluator is called with `generated_answer="RAG stands for Retrieval Augmented Generation"` and expected substring `"Retrieval"`
- **THEN** it SHALL return a Score with `value=1.0`

- **当**`contains` 评估器被调用，`generated_answer="RAG stands for Retrieval Augmented Generation"` 且期望子字符串 `"Retrieval"`
- **则**应返回 `value=1.0` 的 Score

#### Scenario: Is JSON evaluator — 场景：JSON 验证评估器
- **WHEN** the `is_json` evaluator is called with `generated_answer='{"key": "value"}'`
- **THEN** it SHALL return a Score with `value=1.0` and `source="deterministic"`

- **当**`is_json` 评估器被调用，`generated_answer='{"key": "value"}'`
- **则**应返回 `value=1.0` 和 `source="deterministic"` 的 Score

#### Scenario: Regex match evaluator — 场景：正则匹配评估器
- **WHEN** the `regex_match` evaluator is called with `generated_answer="Error code: E-1234"` and pattern `E-\d{4}`
- **THEN** it SHALL return a Score with `value=1.0`

- **当**`regex_match` 评估器被调用，`generated_answer="Error code: E-1234"` 且模式 `E-\d{4}`
- **则**应返回 `value=1.0` 的 Score

### Requirement: BLEU and ROUGE and F1 evaluators — 需求：BLEU、ROUGE 和 F1 评估器
The system SHALL provide `bleu_score`, `rouge_score`, and `f1_score` deterministic evaluators for text similarity measurement against expected answers. These SHALL use standard NLP formulas without LLM calls.

系统应提供 `bleu_score`、`rouge_score` 和 `f1_score` 确定性评估器，用于与预期答案的文本相似度测量。这些应使用标准 NLP 公式，无需 LLM 调用。

#### Scenario: BLEU score evaluation — 场景：BLEU 分数评估
- **WHEN** the `bleu_score` evaluator is called with `generated_answer="the cat sat on the mat"` and `expected_answer="the cat sat on the mat"`
- **THEN** it SHALL return a Score with `value=1.0` and `source="deterministic"`

- **当**`bleu_score` 评估器被调用，`generated_answer="the cat sat on the mat"` 且 `expected_answer="the cat sat on the mat"`
- **则**应返回 `value=1.0` 和 `source="deterministic"` 的 Score

#### Scenario: F1 score with token overlap — 场景：带 token 重叠的 F1 分数
- **WHEN** the `f1_score` evaluator is called with `generated_answer="RAG uses retrieval and generation"` and `expected_answer="RAG combines retrieval with generation"`
- **THEN** it SHALL return a Score with a value between 0.0 and 1.0 based on token-level precision and recall

- **当**`f1_score` 评估器被调用，`generated_answer="RAG uses retrieval and generation"` 且 `expected_answer="RAG combines retrieval with generation"`
- **则**应返回一个介于 0.0 和 1.0 之间的值，基于 token 级精确率和召回率

### Requirement: Content quality LLM-judge evaluators — 需求：内容质量 LLM-judge 评估器
The system SHALL provide 5 content quality evaluators using LLM-as-Judge: `toxicity_detection`, `safety_harmlessness`, `instruction_following`, `coherence`, `fluency`. Each SHALL use a standardized `JudgePromptTemplate` with defined scoring rubrics.

系统应提供 5 个使用 LLM-as-Judge 的内容质量评估器：`toxicity_detection`、`safety_harmlessness`、`instruction_following`、`coherence`、`fluency`。每个应使用标准化的 `JudgePromptTemplate` 和定义的评分量规。

#### Scenario: Toxicity detection with safe content — 场景：安全内容的有毒检测
- **WHEN** the `toxicity_detection` evaluator is called with a non-toxic `generated_answer`
- **THEN** it SHALL return a Score with `value=1.0`, `source="llm_judge"`, and reasoning explaining why the content is safe

- **当**`toxicity_detection` 评估器被调用，`generated_answer` 为非有毒内容
- **则**应返回 `value=1.0`、`source="llm_judge"` 的 Score，并附有解释内容为何安全的推理

#### Scenario: Toxicity detection with harmful content — 场景：有害内容的有毒检测
- **WHEN** the `toxicity_detection` evaluator is called with a harmful `generated_answer`
- **THEN** it SHALL return a Score with `value=0.0` and reasoning identifying the harmful content

- **当**`toxicity_detection` 评估器被调用，`generated_answer` 为有害内容
- **则**应返回 `value=0.0` 的 Score，并附有识别有害内容的推理

#### Scenario: Instruction following evaluation — 场景：指令遵循评估
- **WHEN** the `instruction_following` evaluator is called with `system_prompt="Respond in JSON format"` and `generated_answer='{"answer": "hello"}'`
- **THEN** it SHALL return a Score with `value=1.0` indicating the instruction was followed

- **当**`instruction_following` 评估器被调用，`system_prompt="Respond in JSON format"` 且 `generated_answer='{"answer": "hello"}'`
- **则**应返回 `value=1.0` 的 Score，表示指令被遵循

### Requirement: Citation and grounding evaluators — 需求：引用和基础评估器
The system SHALL provide 4 citation/grounding evaluators: `citation_relevance`, `source_attribution`, `groundedness_check`, `hallucination_detection`. These evaluators assess whether generated answers are properly grounded in retrieved context.

系统应提供 4 个引用/基础评估器：`citation_relevance`、`source_attribution`、`groundedness_check`、`hallucination_detection`。这些评估器评估生成的答案是否正确地基于检索到的上下文。

#### Scenario: Hallucination detection with ungrounded claim — 场景：无基础声明的幻觉检测
- **WHEN** the `hallucination_detection` evaluator is called with `generated_answer` containing a claim not supported by `retrieved_contexts`
- **THEN** it SHALL return a Score with `value=0.0` and reasoning identifying the ungrounded claim

- **当**`hallucination_detection` 评估器被调用，`generated_answer` 包含不受 `retrieved_contexts` 支持的声明
- **则**应返回 `value=0.0` 的 Score，并附有识别无基础声明的推理

#### Scenario: Citation relevance with proper citations — 场景：正确引用的相关性
- **WHEN** the `citation_relevance` evaluator is called with `generated_answer` containing citations that match `retrieved_contexts`
- **THEN** it SHALL return a Score with `value=1.0`

- **当**`citation_relevance` 评估器被调用，`generated_answer` 包含与 `retrieved_contexts` 匹配的引用
- **则**应返回 `value=1.0` 的 Score

### Requirement: Tool and process evaluators — 需求：工具和过程评估器
The system SHALL provide 6 process/tool evaluators: `tool_selection_accuracy`, `tool_trajectory_scoring`, `tool_parameter_accuracy`, `tool_order_correctness`, `reasoning_quality`, `step_validity`. These evaluators assess Agent execution quality beyond final output.

系统应提供 6 个过程/工具评估器：`tool_selection_accuracy`、`tool_trajectory_scoring`、`tool_parameter_accuracy`、`tool_order_correctness`、`reasoning_quality`、`step_validity`。这些评估器评估超越最终输出的 Agent 执行质量。

#### Scenario: Tool selection accuracy with correct tools — 场景：正确工具的选择准确性
- **WHEN** the `tool_selection_accuracy` evaluator is called with `tool_calls` containing only valid tools from the available tool list
- **THEN** it SHALL return a Score with `value=1.0`

- **当**`tool_selection_accuracy` 评估器被调用，`tool_calls` 仅包含可用工具列表中的有效工具
- **则**应返回 `value=1.0` 的 Score

#### Scenario: Tool trajectory scoring — 场景：工具轨迹评分
- **WHEN** the `tool_trajectory_scoring` evaluator is called with a sequence of tool calls that logically progress toward the task goal
- **THEN** it SHALL return a Score reflecting the trajectory quality (0.0–1.0)

- **当**`tool_trajectory_scoring` 评估器被调用，工具调用序列在逻辑上向任务目标推进
- **则**应返回反映轨迹质量的 Score（0.0–1.0）

### Requirement: Multi-turn interaction evaluators — 需求：多轮交互评估器
The system SHALL provide 4 interaction evaluators: `multi_turn_success`, `multi_turn_coherence`, `conversation_quality`, `context_retention`. These evaluators require `conversation_history` in `EvalInput` and assess multi-turn dialogue quality.

系统应提供 4 个交互评估器：`multi_turn_success`、`multi_turn_coherence`、`conversation_quality`、`context_retention`。这些评估器需要 `EvalInput` 中的 `conversation_history`，并评估多轮对话质量。

#### Scenario: Multi-turn success evaluation — 场景：多轮成功评估
- **WHEN** the `multi_turn_success` evaluator is called with `conversation_history` containing a completed multi-turn task
- **THEN** it SHALL return a Score reflecting whether the task was successfully completed across turns

- **当**`multi_turn_success` 评估器被调用，`conversation_history` 包含已完成的多轮任务
- **则**应返回反映任务是否跨轮次成功完成的 Score

#### Scenario: Context retention evaluation — 场景：上下文保留评估
- **WHEN** the `context_retention` evaluator is called with `conversation_history` where the Agent forgot information from earlier turns
- **THEN** it SHALL return a Score with a low value indicating poor context retention

- **当**`context_retention` 评估器被调用，`conversation_history` 中 Agent 忘记了早期轮次的信息
- **则**应返回低值的 Score，表示上下文保留不佳

### Requirement: Generic LLM-as-Judge evaluators — 需求：通用 LLM-as-Judge 评估器
The system SHALL provide 4 generic LLM-judge evaluators: `semantic_similarity`, `rubric_scoring`, `factuality_check`, `llm_rubric`. The `llm_rubric` evaluator SHALL accept a custom rubric string for domain-specific evaluation.

系统应提供 4 个通用 LLM-judge 评估器：`semantic_similarity`、`rubric_scoring`、`factuality_check`、`llm_rubric`。`llm_rubric` 评估器应接受自定义量规字符串进行领域特定评估。

#### Scenario: Custom rubric evaluation — 场景：自定义量规评估
- **WHEN** the `llm_rubric` evaluator is called with a custom rubric `"Score 1.0 if the response includes a code example, 0.0 otherwise"`
- **THEN** it SHALL use that rubric as the judge prompt and return a Score based on the rubric criteria

- **当**`llm_rubric` 评估器被调用，自定义量规 `"如果响应包含代码示例则得 1.0 分，否则 0.0 分"`
- **则**应使用该量规作为 judge 提示，并基于量规标准返回 Score

#### Scenario: Semantic similarity evaluation — 场景：语义相似度评估
- **WHEN** the `semantic_similarity` evaluator is called with `generated_answer` and `expected_answer` that are semantically equivalent but use different wording
- **THEN** it SHALL return a Score with a high value (>= 0.8) reflecting semantic equivalence

- **当**`semantic_similarity` 评估器被调用，`generated_answer` 和 `expected_answer` 语义等价但使用不同措辞
- **则**应返回反映语义等价的高值（>= 0.8）Score

### Requirement: Safety and security evaluators — 需求：安全与安保评估器
The system SHALL provide 3 safety evaluators: `prompt_injection_resistance`, `pii_leakage_detection`, `jailbreak_resistance`. These evaluators test whether the Agent's output is safe from common attack patterns.

系统应提供 3 个安全评估器：`prompt_injection_resistance`、`pii_leakage_detection`、`jailbreak_resistance`。这些评估器测试 Agent 的输出是否对常见攻击模式安全。

#### Scenario: PII leakage detection with sensitive data — 场景：敏感数据的 PII 泄露检测
- **WHEN** the `pii_leakage_detection` evaluator is called with `generated_answer` containing credit card numbers or social security numbers
- **THEN** it SHALL return a Score with `value=0.0` indicating PII leakage was detected

- **当**`pii_leakage_detection` 评估器被调用，`generated_answer` 包含信用卡号或社会安全号码
- **则**应返回 `value=0.0` 的 Score，表示检测到 PII 泄露

#### Scenario: Prompt injection resistance — 场景：提示注入抵抗
- **WHEN** the `prompt_injection_resistance` evaluator is called with `generated_answer` where the Agent followed injected instructions instead of its system prompt
- **THEN** it SHALL return a Score with `value=0.0` indicating the Agent was vulnerable to injection

- **当**`prompt_injection_resistance` 评估器被调用，`generated_answer` 中 Agent 遵循了注入的指令而非其系统提示
- **则**应返回 `value=0.0` 的 Score，表示 Agent 易受注入攻击

### Requirement: Programmatic code execution evaluators — 需求：编程代码执行评估器
The system SHALL provide 3 programmatic evaluators: `python_code_eval`, `javascript_eval` (optional), `custom_callable`. The `python_code_eval` evaluator SHALL execute a user-provided Python function against the evaluation input and return its result as a Score.

系统应提供 3 个编程评估器：`python_code_eval`、`javascript_eval`（可选）、`custom_callable`。`python_code_eval` 评估器应安全地执行用户提供的 Python 函数，针对评估输入并返回其结果作为 Score。

#### Scenario: Python code evaluator with custom function — 场景：带自定义函数的 Python 代码评估器
- **WHEN** the `python_code_eval` evaluator is called with a custom function `lambda input: 1.0 if "RAG" in input.generated_answer else 0.0`
- **THEN** it SHALL execute the function safely and return the resulting Score with `source="deterministic"`

- **当**`python_code_eval` 评估器被调用，自定义函数 `lambda input: 1.0 if "RAG" in input.generated_answer else 0.0`
- **则**应安全地执行该函数并返回结果 Score，`source="deterministic"`

### Requirement: Evaluator registry with decorator auto-registration — 需求：带装饰器自动注册的评估器注册表
The system SHALL provide an evaluator registry in `services/evaluation/registry.py` with a `@register_evaluator(name)` decorator that automatically registers evaluator classes. The registry SHALL support `get_evaluator(name)`, `list_evaluators(category=None)`, and `list_evaluator_names()`.

系统应在 `services/evaluation/registry.py` 中提供一个评估器注册表，使用 `@register_evaluator(name)` 装饰器自动注册评估器类。注册表应支持 `get_evaluator(name)`、`list_evaluators(category=None)` 和 `list_evaluator_names()`。

#### Scenario: Auto-registration via decorator — 场景：通过装饰器自动注册
- **WHEN** a class is decorated with `@register_evaluator("my_custom_eval")`
- **THEN** it SHALL be immediately available via `get_evaluator("my_custom_eval")` and listed in `list_evaluators()`

- **当**一个类被 `@register_evaluator("my_custom_eval")` 装饰
- **则**应立即通过 `get_evaluator("my_custom_eval")` 可用，并出现在 `list_evaluators()` 中

#### Scenario: List evaluators filtered by category — 场景：按类别过滤列出评估器
- **WHEN** `list_evaluators(category="result")` is called
- **THEN** only evaluators with `category="result"` are returned

- **当**调用 `list_evaluators(category="result")`
- **则**仅返回 `category="result"` 的评估器

### Requirement: Standardized JudgePromptTemplate — 需求：标准化的 JudgePromptTemplate
The system SHALL define a `JudgePromptTemplate` dataclass in `services/evaluation/prompt_templates.py` with fields: `scoring_scale` (binary/5_point/continuous), `system_prompt`, `user_prompt_template`, `output_format`, `scoring_rubric`. Every LLM-as-Judge evaluator SHALL use a `JudgePromptTemplate` for its prompt construction.

系统应在 `services/evaluation/prompt_templates.py` 中定义 `JudgePromptTemplate` 数据类，字段包括：`scoring_scale`（binary/5_point/continuous）、`system_prompt`、`user_prompt_template`、`output_format`、`scoring_rubric`。每个 LLM-as-Judge 评估器应使用 `JudgePromptTemplate` 进行提示构建。

#### Scenario: Binary scoring scale template — 场景：二元评分量表模板
- **WHEN** an LLM-judge evaluator uses a template with `scoring_scale="binary"`
- **THEN** the judge prompt SHALL instruct the LLM to return scores of either 0.0 or 1.0 only

- **当**LLM-judge 评估器使用 `scoring_scale="binary"` 的模板
- **则**judge 提示应指示 LLM 仅返回 0.0 或 1.0 的分数

#### Scenario: 5-point scoring scale template — 场景：5 分制评分量表模板
- **WHEN** an LLM-judge evaluator uses a template with `scoring_scale="5_point"`
- **THEN** the judge prompt SHALL instruct the LLM to return one of 0.0, 0.25, 0.5, 0.75, or 1.0 with a rubric description for each level

- **当**LLM-judge 评估器使用 `scoring_scale="5_point"` 的模板
- **则**judge 提示应指示 LLM 返回 0.0、0.25、0.5、0.75 或 1.0 之一，并附有每个级别的量规描述
