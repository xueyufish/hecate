## ADDED Requirements

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
