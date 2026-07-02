## ADDED Requirements

### Requirement: Evaluator abstract base class
The system SHALL provide an `Evaluator` abstract base class in `services/evaluation/evaluator.py` with an async `evaluate(input: EvalInput) -> EvalOutput` method that all evaluators MUST implement.

#### Scenario: Custom evaluator implementation
- **WHEN** a developer creates a class inheriting from `Evaluator`
- **THEN** the class MUST implement the `evaluate()` async method and declare its `name: str` and `description: str` properties

#### Scenario: Evaluator with custom LLM config
- **WHEN** an evaluator is instantiated with an `llm_config` parameter specifying model, temperature, and api_base
- **THEN** the evaluator SHALL use that LLM configuration for all evaluation calls, falling back to the default model if not specified

### Requirement: Structured score output
The system SHALL define a `Score` dataclass in `services/evaluation/types.py` with fields: `metric_name: str`, `value: float` (0.0–1.0), `reasoning: str | None`, `source: str` (one of: "llm_judge", "deterministic", "human").

#### Scenario: Score value range validation
- **WHEN** a Score is created with `value` outside the 0.0–1.0 range
- **THEN** the system SHALL raise a `ValueError`

### Requirement: Evaluation input/output types
The system SHALL define typed dataclasses for evaluation I/O: `EvalInput` (query, retrieved_contexts, generated_answer, expected_answer), `EvalOutput` (scores list, metadata, duration_ms).

#### Scenario: RAG evaluation input
- **WHEN** evaluating a RAG pipeline result
- **THEN** `EvalInput` SHALL contain at minimum: query (str), retrieved_contexts (list[str]), generated_answer (str), and optionally expected_answer (str | None)

#### Scenario: Agent evaluation input
- **WHEN** evaluating an agent response
- **THEN** `EvalInput` SHALL contain at minimum: query (str), generated_answer (str), and optionally expected_answer (str | None) and tool_calls (list[dict] | None)

### Requirement: Evaluation execution engine
The system SHALL provide an `EvaluationEngine` in `services/evaluation/engine.py` that accepts a list of `Evaluator` instances and an `EvaluationDataset`, runs all evaluators against all items, and produces an `EvaluationRunResult` with aggregated scores.

#### Scenario: Batch evaluation execution
- **WHEN** `EvaluationEngine.run(evaluators, dataset)` is called
- **THEN** the engine SHALL execute each evaluator against each dataset item, collect all scores, compute per-metric averages, and return an `EvaluationRunResult`

#### Scenario: Evaluator failure isolation
- **WHEN** an individual evaluator raises an exception during execution
- **THEN** the engine SHALL catch the exception, log it, record a failed score with reasoning="Evaluator error: {message}", and continue with remaining evaluators/items
