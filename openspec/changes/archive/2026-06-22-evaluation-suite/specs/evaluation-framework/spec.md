## MODIFIED Requirements

### Requirement: Evaluation input/output types
The system SHALL define typed dataclasses for evaluation I/O: `EvalInput` (query, retrieved_contexts, generated_answer, expected_answer, tool_calls, conversation_history, system_prompt, agent_id, session_id, metadata), `EvalOutput` (scores list, metadata, duration_ms). The new fields `conversation_history`, `system_prompt`, `agent_id`, and `session_id` SHALL be optional with default values for backward compatibility.

#### Scenario: RAG evaluation input
- **WHEN** evaluating a RAG pipeline result
- **THEN** `EvalInput` SHALL contain at minimum: query (str), retrieved_contexts (list[str]), generated_answer (str), and optionally expected_answer (str | None)

#### Scenario: Agent evaluation input
- **WHEN** evaluating an agent response
- **THEN** `EvalInput` SHALL contain at minimum: query (str), generated_answer (str), and optionally expected_answer (str | None) and tool_calls (list[dict] | None)

#### Scenario: Multi-turn evaluation input
- **WHEN** evaluating a multi-turn conversation
- **THEN** `EvalInput` SHALL contain `conversation_history` (list[dict]) with the full conversation turns for multi-turn evaluators

#### Scenario: Instruction following evaluation input
- **WHEN** evaluating instruction compliance
- **THEN** `EvalInput` SHALL contain `system_prompt` (str | None) for the evaluator to compare against generated output

### Requirement: Evaluation execution engine
The system SHALL provide an `EvaluationEngine` in `services/evaluation/engine.py` that accepts a list of `Evaluator` instances and an `EvaluationDataset`, runs all evaluators against all items, and produces an `EvaluationRunResult` with aggregated scores. The engine SHALL run deterministic evaluators in parallel (via asyncio.gather) before LLM-judge evaluators to optimize throughput.

#### Scenario: Batch evaluation execution
- **WHEN** `EvaluationEngine.run(evaluators, dataset)` is called
- **THEN** the engine SHALL execute each evaluator against each dataset item, collect all scores, compute per-metric averages, and return an `EvaluationRunResult`

#### Scenario: Evaluator failure isolation
- **WHEN** an individual evaluator raises an exception during execution
- **THEN** the engine SHALL catch the exception, log it, record a failed score with reasoning="Evaluator error: {message}", and continue with remaining evaluators/items

#### Scenario: Deterministic evaluators run in parallel
- **WHEN** the engine executes a mix of deterministic and LLM-judge evaluators
- **THEN** deterministic evaluators (source="deterministic") SHALL be executed concurrently via asyncio.gather, while LLM-judge evaluators SHALL be executed sequentially to respect rate limits

#### Scenario: Tag-filtered evaluation run
- **WHEN** `EvaluationEngine.run(evaluators, dataset, tags=["smoke"])` is called
- **THEN** only items with matching tags SHALL be evaluated
