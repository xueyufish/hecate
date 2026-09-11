## Purpose

Lets users run an evaluation dataset end-to-end through a specific workflow
version, capture per-node execution traces without scoring them, score the
final outputs with the existing evaluator registry, and compare two runs to
detect regressions across workflow versions. Pairs with `evaluation-tasks`
(7.2c) and uses `regression-testing`'s comparison surface as the regression
output channel.

## ADDED Requirements

### Requirement: Workflow answer source for offline tasks
The system SHALL accept an `answer_source` value of `workflow` on offline evaluation tasks. When `answer_source=workflow`, the task SHALL require a `workflow_id` referencing an existing workflow in the workspace; an optional `workflow_version` SHALL pin the run to a specific immutable `WorkflowVersionModel` row (when omitted, the system SHALL resolve to the workflow's current latest version at run start and record the resolved version on the run). Tasks with `answer_source=workflow` SHALL also accept `repetitions` (positive integer, default 1) describing how many times each dataset item is executed. Tasks SHALL be rejected at create time when `answer_source=workflow` is paired with an unknown `workflow_id`, a non-positive `repetitions`, or when the resolved `workflow_version` does not exist.

#### Scenario: Create workflow answer-source task with explicit version
- **WHEN** `POST /api/evaluation/tasks` is called with `{"task_type": "offline", "name": "wf-regress", "dataset_id": "...", "evaluators": ["correctness"], "answer_source": "workflow", "workflow_id": "...", "workflow_version": 7, "repetitions": 3}`
- **THEN** the task SHALL be persisted with status `active`, the resolved workflow version locked, and the runnable configuration SHALL expose `workflow_id`, `workflow_version=7`, and `repetitions=3`

#### Scenario: Create workflow answer-source task without version
- **WHEN** the task is created with `workflow_id` but no `workflow_version`
- **THEN** the task SHALL be persisted and the version SHALL be resolved to the workflow's current latest version at the moment the next run starts

#### Scenario: Reject task with unknown workflow
- **WHEN** the task references a `workflow_id` that does not exist in the workspace
- **THEN** the API SHALL return a validation error and SHALL NOT persist the task

#### Scenario: Reject task with non-positive repetitions
- **WHEN** the task is created with `repetitions: 0` or a negative value
- **THEN** the API SHALL return a validation error naming the offending field

### Requirement: Per-item workflow execution and answer capture
When an offline task with `answer_source=workflow` executes, the system SHALL, for each dataset item, execute the workflow once per repetition by invoking the production `WorkflowExecutionService` with the workflow's compiled graph and the item's input, capture the workflow's final output as the item's `generated_answer`, and collect per-node execution data (node id, node type, status, duration, error message) into the run's `node_results` payload. Each invocation SHALL run as a non-streaming single-turn execution with a freshly-allocated session; the runtime request path SHALL NOT be modified. Workflow execution failures SHALL be recorded as a workflow execution error on that item (the score SHALL be marked `error` with status `error` and the error reason recorded) without halting sibling items.

#### Scenario: Execute workflow for each dataset item
- **WHEN** a run executes against a dataset of 5 items with `repetitions: 1`
- **THEN** the system SHALL invoke the workflow exactly once per item (5 invocations total), record each final output as `generated_answer`, and the run's `node_results` SHALL aggregate per-node data across all 5 executions

#### Scenario: Repetitions execute multiple times
- **WHEN** a run executes with `repetitions: 3` against 2 items
- **THEN** the system SHALL invoke the workflow 6 times in total (3 per item), capture 2 `generated_answer` values per item (one per repetition, paired with the run's per-repetition index), and `node_results` SHALL aggregate across all 6 executions

#### Scenario: Workflow execution failure isolates to one item
- **WHEN** a workflow invocation throws for item 3 of 5 and `repetitions: 1`
- **THEN** items 1, 2, 4, and 5 SHALL complete normally; item 3 SHALL be recorded as a workflow execution error and SHALL NOT fail the run as a whole

#### Scenario: Trajectory is captured but not scored
- **WHEN** a workflow run completes successfully across all items
- **THEN** `node_results` SHALL contain the per-node execution data but SHALL NOT contain evaluator scores; scores SHALL only be produced for the final workflow output

### Requirement: Dataset snapshot frozen at run start
The system SHALL, at the start of every offline run, snapshot the dataset's item set into a run-attached `dataset_snapshot` field containing the canonical JSON of every item plus a content `hash`. The run SHALL compare the `hash` against the dataset's current hash at run completion; the resulting run record SHALL expose the snapshot hash, the dataset's current hash, and (when they differ) the list of `item_ids` that changed since snapshot. The snapshot SHALL be the source of truth for "which items the run executed against" — when comparing two runs for regression, the diff endpoint SHALL use the snapshot rather than the live dataset.

#### Scenario: Snapshot captured at run start
- **WHEN** a run starts against a dataset with 10 items
- **THEN** the run record SHALL carry a `dataset_snapshot` with the items' canonical JSON and a hash, persisted before any item executes

#### Scenario: Snapshot hash compared against current dataset hash
- **WHEN** the run completes and the dataset has changed during the run (items added/removed/edited)
- **THEN** the run's `summary` SHALL include `dataset_snapshot_hash`, `dataset_current_hash`, and (when they differ) `dataset_drift_item_ids`

#### Scenario: Diff uses snapshot, not live dataset
- **WHEN** `POST /api/evaluation/runs/compare` is called with two runs whose snapshots differ
- **THEN** the response SHALL align items by snapshot position, not by current dataset ordering, and SHALL report `dataset_drift` describing the hash mismatch

### Requirement: Run diff with paired metric, token, latency, and cost deltas
The system SHALL expose `POST /api/evaluation/runs/compare` accepting `baseline_run_id` and `candidate_run_id`. The response SHALL contain, for each shared metric: `metric`, `baseline_avg`, `candidate_avg`, `delta` (candidate minus baseline), `is_regression` (boolean), and `baseline_workflow_version`, `candidate_workflow_version`. The system SHALL additionally include paired `token_usage_delta`, `latency_delta`, and `cost_delta` (candidate minus baseline) aggregated across all items. When the run snapshots differ, the response SHALL include a `dataset_drift` block; when both runs executed the same workflow graph and the node sets match, the response SHALL include `node_drift` listing per-node `duration_delta`. The `is_regression` threshold SHALL be the same `regression_threshold` configured on the task that produced the candidate run (default 5%). Runs whose `task_id` differs in a way that makes per-item alignment infeasible (different datasets, different evaluators) SHALL be rejected with a clear error rather than silently producing a misleading diff.

#### Scenario: Compare two workflow runs across versions
- **WHEN** `POST /api/evaluation/runs/compare` is called with a baseline run bound to workflow version 6 and a candidate run bound to workflow version 7 sharing the same dataset snapshot hash
- **THEN** the response SHALL include per-metric `{baseline_avg, candidate_avg, delta, is_regression}`, `token_usage_delta`, `latency_delta`, `cost_delta`, and `workflow_version` for each side

#### Scenario: Dataset drift reported when snapshots differ
- **WHEN** the two runs share the same dataset id but the snapshot hashes differ
- **THEN** the response SHALL still compute metric deltas where item positions overlap, AND SHALL include a `dataset_drift` block with `old_hash`, `new_hash`, and `changed_item_ids`

#### Scenario: Incompatible runs rejected
- **WHEN** the two runs reference different datasets or use disjoint evaluator lists
- **THEN** the API SHALL return 422 with a clear error explaining the alignment failure rather than returning a misleading diff

### Requirement: Repetition aggregation in run summary
When `repetitions > 1`, the run's `summary` SHALL include, for each metric: `metric_avg` (mean across all repetitions), `pass_rate` (fraction of repetitions where the metric meets its threshold), and `consistency_rate` (fraction of items where every repetition meets the threshold). When `repetitions = 1`, the summary SHALL include `pass_rate` only. `consistency_rate` SHALL NOT be exposed when `repetitions = 1`.

#### Scenario: Single-repetition run summary
- **WHEN** a run with `repetitions: 1` completes
- **THEN** the summary SHALL include `pass_rate` and `metric_averages` exactly as in 7.2c behavior, and SHALL NOT include `consistency_rate`

#### Scenario: Multi-repetition run summary
- **WHEN** a run with `repetitions: 3` completes and the same item meets the threshold in repetitions 1 and 2 but fails in repetition 3
- **THEN** the item's `pass_rate` SHALL be `0.67` (2 of 3) and the item SHALL NOT contribute to `consistency_rate` (since consistency requires every repetition to pass)

### Requirement: Publish-time evaluation report
The system SHALL, on `POST /workflows/{workflow_id}/publish/{version}`, attach an `evaluation_report` field to the response when at least one workflow evaluation run exists for the publishing version. The report SHALL contain: the latest run's `id`, `pass_rate`, `consistency_rate` (when `repetitions > 1`), and a `comparison_to_published` block containing per-metric deltas against the most recent run of the currently-published version (when such a run exists). The publish API SHALL remain non-blocking — the report is informational, missing runs are surfaced as `evaluation_status: "no_baseline"`, and the publish response SHALL NOT be rejected due to missing or weak evaluation data.

#### Scenario: Publish with comparison against previously published version
- **WHEN** `POST /workflows/{workflow_id}/publish/{version}` is called and the publishing version has an evaluation run while the currently published version also has a recent run
- **THEN** the response SHALL include `evaluation_report` with the new run's summary and `comparison_to_published` containing per-metric deltas

#### Scenario: Publish with no prior baseline
- **WHEN** `POST /workflows/{workflow_id}/publish/{version}` is called and no workflow evaluation run exists for the publishing version
- **THEN** the response SHALL include `evaluation_report: null` and the publish SHALL succeed normally

#### Scenario: Publish with regression in evaluation data
- **WHEN** the publishing version's run shows a regressed metric versus the currently published version
- **THEN** the response SHALL still return 200 OK and the `evaluation_report` SHALL describe the regression; the system SHALL NOT block the publish based on the evaluation data

### Requirement: Run cost guardrails
The system SHALL reject triggering an offline workflow-evaluation run when `items × repetitions` exceeds the task's `max_total_executions` (positive integer, default 1000) with a 400 error naming the violation. The system SHALL also cap the in-flight concurrency of workflow invocations within a single run at the task's `max_in_flight` (positive integer, default 4); executions beyond the cap SHALL queue rather than run in parallel.

#### Scenario: Reject run exceeding total execution cap
- **WHEN** a task with 600 items and `repetitions: 2` is triggered (total 1200) and `max_total_executions` is the default 1000
- **THEN** the trigger API SHALL return 400 with a message identifying the cap and the offending total

#### Scenario: Concurrency cap respected within a run
- **WHEN** a run is executing with `max_in_flight: 2` and 10 items are queued
- **THEN** at most 2 workflow invocations SHALL be in flight at any time; the remaining items SHALL be processed as slots free up

### Requirement: CLI eval run with three-state exit code
The system SHALL provide a `hecate workflow eval run` CLI subcommand accepting `--dataset` (dataset id or name), `--workflow-id`, `--workflow-version` (optional), `--repetitions` (optional, defaults to task default), and `--baseline-run-id` (optional). On completion, the CLI SHALL print a JSON result to stdout and exit with code 0 when no regression is detected and the dataset snapshot has not drifted; exit code 2 when the snapshot has drifted (warning, not failure); and exit code 3 when at least one metric regression is detected. The CLI SHALL NOT post PR comments; report rendering is left to the calling CI system.

#### Scenario: CLI run exits 0 on success
- **WHEN** the CLI run completes with all metrics within threshold and no dataset drift
- **THEN** the exit code SHALL be 0 and the stdout JSON SHALL include `passed: true`

#### Scenario: CLI run exits 2 on dataset drift
- **WHEN** the dataset snapshot hash differs from the dataset's current hash but no metric regression occurred
- **THEN** the exit code SHALL be 2 and the JSON SHALL include `dataset_drift: {old_hash, new_hash, changed_item_ids}` with `passed: true` and a `warnings` array

#### Scenario: CLI run exits 3 on regression
- **WHEN** at least one metric drops more than its configured threshold versus the baseline
- **THEN** the exit code SHALL be 3 and the JSON SHALL include `regressions: [{metric, baseline_avg, candidate_avg, delta}]` with `passed: false`