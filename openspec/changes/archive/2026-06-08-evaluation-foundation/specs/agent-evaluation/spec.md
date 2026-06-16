## ADDED Requirements

### Requirement: Correctness evaluator
The system SHALL provide a `CorrectnessEvaluator` that compares generated answers against expected answers using LLM-as-Judge. It SHALL assess factual accuracy and completeness.

#### Scenario: Compare against ground truth
- **WHEN** a generated answer and expected answer are provided
- **THEN** the evaluator SHALL use LLM-as-Judge to return a Score with `metric_name="correctness"` and `value` reflecting factual accuracy (1.0 = fully correct, 0.0 = completely wrong)

#### Scenario: No expected answer provided
- **WHEN** a correctness evaluation is requested without an expected answer
- **THEN** the evaluator SHALL return a Score with `value=-1.0` and `reasoning="No expected answer provided"`

### Requirement: Relevancy evaluator
The system SHALL provide a `RelevancyEvaluator` that measures how well the agent's response addresses the user's query using LLM-as-Judge.

#### Scenario: Evaluate response relevance
- **WHEN** an agent response is evaluated against the user query
- **THEN** the evaluator SHALL return a Score with `metric_name="relevancy"` indicating whether the response directly addresses the question

#### Scenario: Off-topic response
- **WHEN** an agent response is unrelated to the user query
- **THEN** the evaluator SHALL return `value` close to 0.0 with reasoning explaining the mismatch

### Requirement: Completeness evaluator
The system SHALL provide a `CompletenessEvaluator` that measures whether the agent's response covers all aspects of the user's query using LLM-as-Judge.

#### Scenario: Evaluate multi-aspect query coverage
- **WHEN** a user query has multiple aspects and the response addresses some but not all
- **THEN** the evaluator SHALL return a Score with `metric_name="completeness"` and `value` proportional to the fraction of aspects addressed

#### Scenario: Fully complete response
- **WHEN** an agent response addresses all aspects of the user query
- **THEN** the evaluator SHALL return `value=1.0`
