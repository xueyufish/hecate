## ADDED Requirements

### Requirement: Context Precision evaluator
The system SHALL provide a `ContextPrecisionEvaluator` that measures whether relevant items in retrieved context are ranked higher. It SHALL use Ragas's `ContextPrecision` metric when `ragas` is installed.

#### Scenario: Evaluate ranked retrieval results
- **WHEN** a RAG query returns contexts ranked by relevance and a ground truth answer is provided
- **THEN** the evaluator SHALL return a Score with `metric_name="context_precision"` and `value` between 0.0 and 1.0 indicating whether relevant contexts appear at the top

#### Scenario: Ragas not installed
- **WHEN** a user attempts to use `ContextPrecisionEvaluator` without `ragas` installed
- **THEN** the system SHALL raise an `ImportError` with message explaining how to install: `pip install hecate[rag]`

### Requirement: Context Recall evaluator
The system SHALL provide a `ContextRecallEvaluator` that measures whether retrieved context aligns with the expected answer. It SHALL use Ragas's `ContextRecall` metric.

#### Scenario: Evaluate context coverage
- **WHEN** a RAG query returns contexts and an expected answer is provided
- **THEN** the evaluator SHALL return a Score with `metric_name="context_recall"` indicating how much of the expected answer is covered by retrieved context

### Requirement: Faithfulness evaluator
The system SHALL provide a `FaithfulnessEvaluator` that measures whether the generated answer is factually consistent with retrieved context (hallucination detection).

#### Scenario: Detect hallucinated claims
- **WHEN** a generated answer contains claims not supported by retrieved context
- **THEN** the evaluator SHALL return a Score with `metric_name="faithfulness"` and `value` penalized for each unsupported claim

#### Scenario: Fully faithful answer
- **WHEN** every claim in the generated answer can be traced to retrieved context
- **THEN** the evaluator SHALL return `value=1.0`

### Requirement: Answer Relevancy evaluator
The system SHALL provide an `AnswerRelevancyEvaluator` that measures how relevant the generated answer is to the user's question.

#### Scenario: Evaluate answer relevance
- **WHEN** a generated answer is evaluated against the original query
- **THEN** the evaluator SHALL return a Score with `metric_name="answer_relevancy"` indicating semantic similarity between answer and question intent
