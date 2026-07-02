## Purpose

This specification defines the RAG Evaluation capability — a set of evaluators that measure RAG pipeline quality using Ragas-backed metrics for context precision, context recall, faithfulness, and answer relevancy.
## Requirements
### Requirement: Context Precision evaluator
The system SHALL provide a `ContextPrecisionEvaluator` that measures whether relevant items in retrieved context are ranked higher. It SHALL use Ragas's `ContextPrecision` metric when `ragas` is installed. The evaluator SHALL accept `generated_answer` from the evaluation item when available, or invoke the RAG pipeline automatically when `generated_answer` is empty.

#### Scenario: Evaluate with pre-generated answer
- **WHEN** an evaluation item has a non-empty `generated_answer` field
- **THEN** the evaluator SHALL use that answer for evaluation instead of invoking the RAG pipeline

#### Scenario: Evaluate with RAG pipeline auto-generation
- **WHEN** an evaluation item has an empty `generated_answer` field and the evaluation run specifies `answer_source="pipeline"`
- **THEN** the system SHALL invoke the RAG pipeline with the item's query and retrieved contexts to generate an answer, then evaluate the generated answer

#### Scenario: Ragas not installed
- **WHEN** a user attempts to use `ContextPrecisionEvaluator` without `ragas` installed
- **THEN** the system SHALL raise an `ImportError` with message explaining how to install: `pip install hecate[rag]`

### Requirement: Context Recall evaluator
The system SHALL provide a `ContextRecallEvaluator` that measures whether retrieved context aligns with the expected answer. It SHALL use Ragas's `ContextRecall` metric. The evaluator SHALL support both pre-generated and pipeline-generated answers.

#### Scenario: Evaluate context coverage with pipeline-generated answer
- **WHEN** a RAG evaluation runs with `answer_source="pipeline"` and an item has no `generated_answer`
- **THEN** the evaluator SHALL invoke the RAG pipeline to generate an answer, then measure context recall against the expected answer

### Requirement: Faithfulness evaluator
The system SHALL provide a `FaithfulnessEvaluator` that measures whether the generated answer is factually consistent with retrieved context (hallucination detection). The evaluator SHALL support both pre-generated and pipeline-generated answers.

#### Scenario: Detect hallucinated claims with pipeline-generated answer
- **WHEN** a RAG pipeline generates an answer containing claims not supported by retrieved context
- **THEN** the evaluator SHALL return a Score with `metric_name="faithfulness"` and `value` penalized for each unsupported claim

### Requirement: Answer Relevancy evaluator
The system SHALL provide an `AnswerRelevancyEvaluator` that measures how relevant the generated answer is to the user's question. The evaluator SHALL support both pre-generated and pipeline-generated answers.

#### Scenario: Evaluate answer relevance with pipeline-generated answer
- **WHEN** a RAG pipeline generates an answer for a query
- **THEN** the evaluator SHALL return a Score with `metric_name="answer_relevancy"` indicating semantic similarity between answer and question intent

