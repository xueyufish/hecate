## Purpose

This specification defines the Evaluation Framework — the core abstractions and execution engine for running batch evaluations, including the Evaluator ABC, Score dataclass, EvalInput/EvalOutput types, and the EvaluationEngine that orchestrates evaluators against datasets.
## Requirements
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

### Requirement: Evaluation dataset item with generated answer
The system SHALL extend `EvaluationItemModel` with a `generated_answer` field (TEXT, nullable, default NULL). When provided, this field SHALL contain the system-generated answer (from RAG pipeline or agent execution) for evaluation. The `EvaluationItemCreateSchema` SHALL accept an optional `generated_answer` field. The `EvaluationItemReadSchema` SHALL include `generated_answer`.

#### Scenario: Create item with pre-generated answer
- **WHEN** a POST request is sent to create an evaluation item with `{"query": "...", "generated_answer": "...", "expected_answer": "..."}`
- **THEN** the system SHALL store the `generated_answer` and use it during evaluation runs

#### Scenario: Create item without generated answer
- **WHEN** a POST request creates an evaluation item without `generated_answer`
- **THEN** the system SHALL store NULL and allow the evaluation engine to generate the answer via pipeline

### Requirement: Evaluation engine answer source modes
The system SHALL support an `answer_source` parameter in evaluation run creation: `"manual"` (use item's `generated_answer` field), `"pipeline"` (invoke RAG pipeline or agent to generate answers), `"auto"` (default — use `generated_answer` if present, otherwise invoke pipeline). The `EvaluationRunCreateSchema` SHALL accept an optional `answer_source` field.

#### Scenario: Manual mode evaluation
- **WHEN** an evaluation run is created with `answer_source="manual"` and items have `generated_answer` populated
- **THEN** the engine SHALL evaluate each item using the provided `generated_answer` without invoking any pipeline

#### Scenario: Pipeline mode evaluation for RAG
- **WHEN** an evaluation run is created with `answer_source="pipeline"` and the dataset is associated with a knowledge base
- **THEN** the engine SHALL invoke the RAG pipeline for each item to generate an answer, then evaluate it

#### Scenario: Auto mode fallback
- **WHEN** an evaluation run is created with `answer_source="auto"` (or omitted) and an item has `generated_answer=NULL`
- **THEN** the engine SHALL invoke the pipeline to generate the answer before evaluation

