## ADDED Requirements

### Requirement: Four-layer evaluator taxonomy
The system SHALL organize all built-in evaluators into four categories: Result Layer (output quality), Process Layer (tool/reasoning correctness), Interaction Layer (multi-turn coherence), and Generic/Programmatic Layer (deterministic, LLM-judge, code execution, safety). Each evaluator SHALL declare its category via a `category` class attribute.

#### Scenario: List evaluators by category
- **WHEN** `GET /api/evaluation/evaluators?category=result` is called
- **THEN** only evaluators with `category="result"` are returned

#### Scenario: List all evaluators with categories
- **WHEN** `GET /api/evaluation/evaluators` is called without a category filter
- **THEN** all registered evaluators are returned grouped by category, each with name, description, category, source type (deterministic/llm_judge), and required input fields

### Requirement: Deterministic format evaluators
The system SHALL provide 6 deterministic format evaluators that do not require LLM calls: `exact_match`, `contains`, `contains_any`, `regex_match`, `is_json`, `format_check`. Each SHALL produce a Score with `source="deterministic"` and execute in sub-millisecond time.

#### Scenario: Exact match evaluator
- **WHEN** the `exact_match` evaluator is called with `generated_answer="Paris"` and `expected_answer="Paris"`
- **THEN** it SHALL return a Score with `value=1.0` and `source="deterministic"`

#### Scenario: Contains evaluator with substring
- **WHEN** the `contains` evaluator is called with `generated_answer="RAG stands for Retrieval Augmented Generation"` and expected substring `"Retrieval"`
- **THEN** it SHALL return a Score with `value=1.0`

#### Scenario: Is JSON evaluator
- **WHEN** the `is_json` evaluator is called with `generated_answer='{"key": "value"}'`
- **THEN** it SHALL return a Score with `value=1.0` and `source="deterministic"`

#### Scenario: Regex match evaluator
- **WHEN** the `regex_match` evaluator is called with `generated_answer="Error code: E-1234"` and pattern `E-\d{4}`
- **THEN** it SHALL return a Score with `value=1.0`

### Requirement: BLEU and ROUGE and F1 evaluators
The system SHALL provide `bleu_score`, `rouge_score`, and `f1_score` deterministic evaluators for text similarity measurement against expected answers. These SHALL use standard NLP formulas without LLM calls.

#### Scenario: BLEU score evaluation
- **WHEN** the `bleu_score` evaluator is called with `generated_answer="the cat sat on the mat"` and `expected_answer="the cat sat on the mat"`
- **THEN** it SHALL return a Score with `value=1.0` and `source="deterministic"`

#### Scenario: F1 score with token overlap
- **WHEN** the `f1_score` evaluator is called with `generated_answer="RAG uses retrieval and generation"` and `expected_answer="RAG combines retrieval with generation"`
- **THEN** it SHALL return a Score with a value between 0.0 and 1.0 based on token-level precision and recall

### Requirement: Content quality LLM-judge evaluators
The system SHALL provide 5 content quality evaluators using LLM-as-Judge: `toxicity_detection`, `safety_harmlessness`, `instruction_following`, `coherence`, `fluency`. Each SHALL use a standardized `JudgePromptTemplate` with defined scoring rubrics.

#### Scenario: Toxicity detection with safe content
- **WHEN** the `toxicity_detection` evaluator is called with a non-toxic `generated_answer`
- **THEN** it SHALL return a Score with `value=1.0`, `source="llm_judge"`, and reasoning explaining why the content is safe

#### Scenario: Toxicity detection with harmful content
- **WHEN** the `toxicity_detection` evaluator is called with a harmful `generated_answer`
- **THEN** it SHALL return a Score with `value=0.0` and reasoning identifying the harmful content

#### Scenario: Instruction following evaluation
- **WHEN** the `instruction_following` evaluator is called with `system_prompt="Respond in JSON format"` and `generated_answer='{"answer": "hello"}'`
- **THEN** it SHALL return a Score with `value=1.0` indicating the instruction was followed

### Requirement: Citation and grounding evaluators
The system SHALL provide 4 citation/grounding evaluators: `citation_relevance`, `source_attribution`, `groundedness_check`, `hallucination_detection`. These evaluators assess whether generated answers are properly grounded in retrieved context.

#### Scenario: Hallucination detection with ungrounded claim
- **WHEN** the `hallucination_detection` evaluator is called with `generated_answer` containing a claim not supported by `retrieved_contexts`
- **THEN** it SHALL return a Score with `value=0.0` and reasoning identifying the ungrounded claim

#### Scenario: Citation relevance with proper citations
- **WHEN** the `citation_relevance` evaluator is called with `generated_answer` containing citations that match `retrieved_contexts`
- **THEN** it SHALL return a Score with `value=1.0`

### Requirement: Tool and process evaluators
The system SHALL provide 6 process/tool evaluators: `tool_selection_accuracy`, `tool_trajectory_scoring`, `tool_parameter_accuracy`, `tool_order_correctness`, `reasoning_quality`, `step_validity`. These evaluators assess Agent execution quality beyond final output.

#### Scenario: Tool selection accuracy with correct tools
- **WHEN** the `tool_selection_accuracy` evaluator is called with `tool_calls` containing only valid tools from the available tool list
- **THEN** it SHALL return a Score with `value=1.0`

#### Scenario: Tool trajectory scoring
- **WHEN** the `tool_trajectory_scoring` evaluator is called with a sequence of tool calls that logically progress toward the task goal
- **THEN** it SHALL return a Score reflecting the trajectory quality (0.0–1.0)

### Requirement: Multi-turn interaction evaluators
The system SHALL provide 4 interaction evaluators: `multi_turn_success`, `multi_turn_coherence`, `conversation_quality`, `context_retention`. These evaluators require `conversation_history` in `EvalInput` and assess multi-turn dialogue quality.

#### Scenario: Multi-turn success evaluation
- **WHEN** the `multi_turn_success` evaluator is called with `conversation_history` containing a completed multi-turn task
- **THEN** it SHALL return a Score reflecting whether the task was successfully completed across turns

#### Scenario: Context retention evaluation
- **WHEN** the `context_retention` evaluator is called with `conversation_history` where the Agent forgot information from earlier turns
- **THEN** it SHALL return a Score with a low value indicating poor context retention

### Requirement: Generic LLM-as-Judge evaluators
The system SHALL provide 4 generic LLM-judge evaluators: `semantic_similarity`, `rubric_scoring`, `factuality_check`, `llm_rubric`. The `llm_rubric` evaluator SHALL accept a custom rubric string for domain-specific evaluation.

#### Scenario: Custom rubric evaluation
- **WHEN** the `llm_rubric` evaluator is called with a custom rubric `"Score 1.0 if the response includes a code example, 0.0 otherwise"`
- **THEN** it SHALL use that rubric as the judge prompt and return a Score based on the rubric criteria

#### Scenario: Semantic similarity evaluation
- **WHEN** the `semantic_similarity` evaluator is called with `generated_answer` and `expected_answer` that are semantically equivalent but use different wording
- **THEN** it SHALL return a Score with a high value (>= 0.8) reflecting semantic equivalence

### Requirement: Safety and security evaluators
The system SHALL provide 3 safety evaluators: `prompt_injection_resistance`, `pii_leakage_detection`, `jailbreak_resistance`. These evaluators test whether the Agent's output is safe from common attack patterns.

#### Scenario: PII leakage detection with sensitive data
- **WHEN** the `pii_leakage_detection` evaluator is called with `generated_answer` containing credit card numbers or social security numbers
- **THEN** it SHALL return a Score with `value=0.0` indicating PII leakage was detected

#### Scenario: Prompt injection resistance
- **WHEN** the `prompt_injection_resistance` evaluator is called with `generated_answer` where the Agent followed injected instructions instead of its system prompt
- **THEN** it SHALL return a Score with `value=0.0` indicating the Agent was vulnerable to injection

### Requirement: Programmatic code execution evaluators
The system SHALL provide 3 programmatic evaluators: `python_code_eval`, `javascript_eval` (optional), `custom_callable`. The `python_code_eval` evaluator SHALL execute a user-provided Python function against the evaluation input and return its result as a Score.

#### Scenario: Python code evaluator with custom function
- **WHEN** the `python_code_eval` evaluator is called with a custom function `lambda input: 1.0 if "RAG" in input.generated_answer else 0.0`
- **THEN** it SHALL execute the function safely and return the resulting Score with `source="deterministic"`

### Requirement: Evaluator registry with decorator auto-registration
The system SHALL provide an evaluator registry in `services/evaluation/registry.py` with a `@register_evaluator(name)` decorator that automatically registers evaluator classes. The registry SHALL support `get_evaluator(name)`, `list_evaluators(category=None)`, and `list_evaluator_names()`.

#### Scenario: Auto-registration via decorator
- **WHEN** a class is decorated with `@register_evaluator("my_custom_eval")`
- **THEN** it SHALL be immediately available via `get_evaluator("my_custom_eval")` and listed in `list_evaluators()`

#### Scenario: List evaluators filtered by category
- **WHEN** `list_evaluators(category="result")` is called
- **THEN** only evaluators with `category="result"` are returned

### Requirement: Standardized JudgePromptTemplate
The system SHALL define a `JudgePromptTemplate` dataclass in `services/evaluation/prompt_templates.py` with fields: `scoring_scale` (binary/5_point/continuous), `system_prompt`, `user_prompt_template`, `output_format`, `scoring_rubric`. Every LLM-as-Judge evaluator SHALL use a `JudgePromptTemplate` for its prompt construction.

#### Scenario: Binary scoring scale template
- **WHEN** an LLM-judge evaluator uses a template with `scoring_scale="binary"`
- **THEN** the judge prompt SHALL instruct the LLM to return scores of either 0.0 or 1.0 only

#### Scenario: 5-point scoring scale template
- **WHEN** an LLM-judge evaluator uses a template with `scoring_scale="5_point"`
- **THEN** the judge prompt SHALL instruct the LLM to return one of 0.0, 0.25, 0.5, 0.75, or 1.0 with a rubric description for each level
