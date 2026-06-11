## Purpose

This specification defines the Agent Evaluation capability — a set of LLM-as-Judge evaluators that measure agent performance on dimensions such as answer correctness, relevance, completeness, tool call accuracy, and task completion.
## Requirements
### Requirement: Correctness evaluator
The system SHALL provide a `CorrectnessEvaluator` that compares generated answers against expected answers using LLM-as-Judge. It SHALL assess factual accuracy and completeness. The evaluator SHALL use `generated_answer` from the evaluation item when available.

#### Scenario: Compare against ground truth with pre-generated answer
- **WHEN** a generated answer and expected answer are provided in the evaluation item
- **THEN** the evaluator SHALL use LLM-as-Judge to return a Score reflecting factual accuracy

#### Scenario: No expected answer provided
- **WHEN** a correctness evaluation is requested without an expected answer
- **THEN** the evaluator SHALL return a Score with `value=-1.0` and `reasoning="No expected_answer provided"`

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

### Requirement: Tool call accuracy evaluator
The system SHALL provide a `ToolCallAccuracyEvaluator` that measures whether the agent selected the correct tools with correct parameters. It SHALL use LLM-as-Judge to compare actual tool calls against expected tool calls provided in the evaluation item. The metric SHALL score both tool selection correctness and parameter accuracy.

#### Scenario: Correct tool selection and parameters
- **WHEN** an agent evaluation item has `tool_calls` containing `[{"name": "search_web", "parameters": {"query": "weather"}}]` and the expected tool calls match
- **THEN** the evaluator SHALL return a Score with `metric_name="tool_call_accuracy"` and `value` close to 1.0

#### Scenario: Wrong tool selected
- **WHEN** an agent selected `send_email` instead of the expected `search_web`
- **THEN** the evaluator SHALL return a Score with `value` close to 0.0 and `reasoning` explaining the mismatch

#### Scenario: No tool calls provided
- **WHEN** a tool call accuracy evaluation is requested without `tool_calls` in the evaluation item
- **THEN** the evaluator SHALL return a Score with `value=-1.0` and `reasoning="No tool_calls provided"`

### Requirement: Task completion evaluator
The system SHALL provide a `TaskCompletionEvaluator` that measures whether the agent successfully completed the assigned task. It SHALL use LLM-as-Judge to assess whether the final response demonstrates task completion, considering the original query, any intermediate steps, and the final answer.

#### Scenario: Task fully completed
- **WHEN** an agent response demonstrates that the assigned task was fully accomplished
- **THEN** the evaluator SHALL return a Score with `metric_name="task_completion"` and `value=1.0`

#### Scenario: Task partially completed
- **WHEN** an agent response addresses some aspects of the task but misses others
- **THEN** the evaluator SHALL return a Score with `metric_name="task_completion"` and `value` proportional to the fraction completed

#### Scenario: Task not attempted
- **WHEN** an agent response is unrelated to the assigned task
- **THEN** the evaluator SHALL return a Score with `metric_name="task_completion"` and `value` close to 0.0

