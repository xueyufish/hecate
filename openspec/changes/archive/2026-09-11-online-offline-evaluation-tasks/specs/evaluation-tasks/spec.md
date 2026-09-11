## Purpose

Defines reusable evaluation tasks: persistent offline tasks that run dataset × evaluator batches asynchronously (optionally invoking the agent under test), and always-on online tasks that sample completed production traces and auto-score them with the registered evaluators. Scores land in a target-typed score store queryable by task, target, and session.

## ADDED Requirements

### Requirement: Evaluation task definition and CRUD
The system SHALL provide CRUD endpoints at `/api/evaluation/tasks` for evaluation tasks. A task SHALL have a `task_type` of `offline` or `online`. An offline task SHALL reference a `dataset_id` and a non-empty list of registered evaluator names, with optional `answer_source` (`manual` | `pipeline` | `agent`, default `manual`), optional `threshold`, and optional `baseline_run_id`. An online task SHALL reference an `agent_id` filter, a non-empty list of registered evaluator names, and a `sampling_rate` in `(0, 1]`. All tasks SHALL be scoped to the creating workspace and invisible to other workspaces. Tasks SHALL have a `status` of `active` or `disabled` (new tasks default to `active`; only online tasks use `disabled` semantics to stop scoring).

#### Scenario: Create offline task
- **WHEN** `POST /api/evaluation/tasks` is called with `{"task_type": "offline", "name": "nightly-regression", "dataset_id": "...", "evaluators": ["correctness", "faithfulness"], "answer_source": "agent", "threshold": 0.7}`
- **THEN** the task SHALL be persisted with status `active` and returned with its `id`, `task_type`, and resolved configuration

#### Scenario: Reject online task with invalid sampling rate
- **WHEN** an online task is created with `sampling_rate` of `0` or greater than `1`
- **THEN** the API SHALL return a validation error and SHALL NOT persist the task

#### Scenario: Reject task referencing unknown evaluator
- **WHEN** a task is created with an evaluator name that is not registered
- **THEN** the API SHALL return a validation error naming the unresolvable evaluator

#### Scenario: Workspace isolation
- **WHEN** a task belonging to workspace A is requested from workspace B
- **THEN** the API SHALL behave as if the task does not exist

### Requirement: Offline task run execution
The system SHALL expose `POST /api/evaluation/tasks/{task_id}/runs` that queues an asynchronous run of the task's evaluators against the task's dataset and returns `202` with a run identifier in `pending` state. The run SHALL execute in the background with the lifecycle `pending → running → completed | failed`, and SHALL be persisted as an evaluation run linked to the task via `task_id`. When `answer_source` is `manual`, the engine SHALL score the item's stored `generated_answer`. When `answer_source` is `pipeline` or `agent` and the item has no stored answer, the engine SHALL generate the answer — via the RAG pipeline fallback or by invoking the agent under test (one invocation per item), respectively. When `threshold` is configured, the completed run SHALL compute per-item pass/fail and aggregate `summary` (`total_items`, `passed_items`, `failed_items`, `pass_rate`, `metric_averages`); when `baseline_run_id` is configured, the summary SHALL additionally flag regressed metrics against the baseline.

#### Scenario: Trigger asynchronous offline run
- **WHEN** `POST /api/evaluation/tasks/{task_id}/runs` is called on an active offline task
- **THEN** the API SHALL return `202` with a run identifier in `pending` state, and the run SHALL transition to `completed` (or `failed`) without requiring the HTTP request to stay open

#### Scenario: Agent answer source invokes the agent under test
- **WHEN** a task with `answer_source: "agent"` runs against items lacking `generated_answer`
- **THEN** the engine SHALL invoke the agent under test once per item with the item query, score the returned answer, and record failures of the agent invocation as error scores (`value = -1.0`) without failing sibling items

#### Scenario: Threshold produces pass/fail summary
- **WHEN** a run of a task with `threshold` configured reaches `completed`
- **THEN** the run SHALL carry a `summary` with `total_items`, `passed_items`, `failed_items`, `pass_rate`, and `metric_averages`

#### Scenario: Baseline comparison flags regressions
- **WHEN** a task with `baseline_run_id` completes a run where a metric average dropped more than the threshold delta versus the baseline run
- **THEN** the summary SHALL mark that metric as regressed

### Requirement: Online task lifecycle
The system SHALL expose `POST /api/evaluation/tasks/{task_id}/enable` and `POST /api/evaluation/tasks/{task_id}/disable` for online tasks. Enabling SHALL validate that all referenced evaluators are registered and that `sampling_rate` is in `(0, 1]`; a valid enabled task SHALL score production traces continuously until disabled. Disabling SHALL stop new sampling and scoring while preserving already-produced scores. Enabling or running SHALL be rejected for tasks in `disabled` state.

#### Scenario: Enable online task
- **WHEN** `enable` is called on a valid online task
- **THEN** the task status SHALL become `active` and production traces SHALL begin to be sampled and scored

#### Scenario: Disable stops scoring
- **WHEN** `disable` is called on an enabled online task
- **THEN** the task SHALL stop sampling new traces and previously produced scores SHALL remain queryable

#### Scenario: Enable with unregistered evaluator is rejected
- **WHEN** `enable` is called on an online task referencing an evaluator name not in the registry
- **THEN** the API SHALL return a validation error and the task SHALL remain `disabled`

### Requirement: Online trace sampling and scoring
For each enabled online task, the system SHALL asynchronously discover completed root traces (trace records of type `trace` with `status` `completed`) created after the task's last scan position, filter them by the task's `agent_id`, select a deterministic sample based on `sampling_rate` (the same trace ID yields the same sample decision), and cap each scan cycle at `max_traces_per_cycle` traces. For each sampled trace, the system SHALL build an evaluation input from the session's persisted execution events within the trace's time window — the user query and generated answer from the final user/assistant messages in the window, preceding messages as conversation history, and tool calls from the window's tool-call records — and produce one score record per evaluator metric with `reasoning` preserved. Scoring SHALL be idempotent per `(task_id, target_type, target_id, metric_name)` — re-scanning SHALL NOT duplicate score records. An evaluator failure on one trace SHALL be recorded as an error score (`value = -1.0`, status `error`) and SHALL NOT halt scoring of other traces.

#### Scenario: Sampled trace is scored
- **WHEN** an online task with `sampling_rate: 1.0` and evaluator `relevancy` is enabled and a root trace for the filtered agent completes
- **THEN** a score record for `relevancy` SHALL be persisted with `target_type: "trace"`, the trace's `target_id`, and the trace's `session_id`

#### Scenario: Deterministic sampling controls volume
- **WHEN** an online task with `sampling_rate: 0.1` processes a stream of trace IDs
- **THEN** the same trace ID SHALL always produce the same sample decision, and approximately the configured fraction of traces SHALL be scored

#### Scenario: Scan cycle volume cap
- **WHEN** more than `max_traces_per_cycle` candidate traces are pending in one scan cycle
- **THEN** at most `max_traces_per_cycle` traces SHALL be scored in that cycle and the remainder SHALL be picked up in subsequent cycles

#### Scenario: Duplicate scan does not duplicate scores
- **WHEN** the same completed trace is encountered again by the scanner after already being scored by the same task
- **THEN** no additional score records SHALL be created for that `(task_id, target_id, metric_name)` triple

#### Scenario: Evaluator error is isolated
- **WHEN** an evaluator raises an error while scoring a sampled trace
- **THEN** an error score with `value = -1.0` and status `error` SHALL be recorded with the failure reason, and other traces and evaluators SHALL continue to be scored

### Requirement: Target-typed score storage and query
The system SHALL persist online evaluation scores in a dedicated target-typed score store where each record SHALL contain: `task_id`, `target_type` (v1: `trace`), `target_id`, `session_id`, `agent_id` (denormalized from the target trace), `metric_name`, `value`, `reasoning`, `source`, `status` (`completed` | `error`), and timestamps. The system SHALL expose `GET /api/evaluation/scores` with pagination and optional filters `task_id`, `target_id`, `session_id`, `metric_name`, and `status`. Online scores SHALL be stored separately from offline run scores; offline run score APIs SHALL be unaffected.

#### Scenario: Query scores by task
- **WHEN** `GET /api/evaluation/scores?task_id={id}` is called
- **THEN** all score records produced by that task SHALL be returned ordered by creation time with pagination

#### Scenario: Query scores by session
- **WHEN** `GET /api/evaluation/scores?session_id={id}` is called
- **THEN** all score records whose target trace belongs to that session SHALL be returned

#### Scenario: Online scores do not leak into run scores
- **WHEN** `GET /api/evaluation/runs/{run_id}/scores` is called for an offline run
- **THEN** only that run's offline scores SHALL be returned; online task scores SHALL not appear

### Requirement: Task run listing
The system SHALL expose `GET /api/evaluation/tasks/{task_id}/runs` returning the task's runs with pagination, each entry containing the run identifier, lifecycle `status`, `started_at`/`completed_at`, and `summary` when computed.

#### Scenario: List runs of an offline task
- **WHEN** `GET /api/evaluation/tasks/{task_id}/runs` is called for an offline task with prior runs
- **THEN** the response SHALL list those runs in reverse creation order with their status and summary
