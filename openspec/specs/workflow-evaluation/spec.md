# Capability: workflow-evaluation

> Synced from archive of change `workflow-evaluation` (7.3).
> Lets users run an evaluation dataset end-to-end through a specific workflow
> version, capture per-node execution traces without scoring them, score the
> final outputs with the existing evaluator registry, and compare two runs to
> detect regressions across workflow versions. Pairs with `evaluation-tasks`
> (7.2c) and uses `regression-testing`'s comparison surface as the regression
> output channel.
> Later sync: `known-bad-exemption` (7.3c) added known-bad exclusion semantics
> to run aggregation and the exemption-aware snapshot rules.

## Purpose

Lets users run an evaluation dataset end-to-end through a specific workflow
version, capture per-node execution traces without scoring them, score the
final outputs with the existing evaluator registry, and compare two runs to
detect regressions across workflow versions. Pairs with `evaluation-tasks`
(7.2c) and uses `regression-testing`'s comparison surface as the regression
output channel.

## Requirements

### Requirement: Workflow answer source for offline tasks
The system SHALL accept an `answer_source` value of `workflow` on offline evaluation tasks. When `answer_source=workflow`, the task SHALL require a `workflow_id` referencing an existing workflow in the workspace; an optional `workflow_version` SHALL pin the run to a specific immutable `WorkflowVersionModel` row (when omitted, the system SHALL resolve to the workflow's current latest version at run start and record the resolved version on the run). Tasks with `answer_source=workflow` SHALL also accept `repetitions` (positive integer, default 1) describing how many times each dataset item is executed. Tasks SHALL be rejected at create time when `answer_source=workflow` is paired with an unknown `workflow_id`, a non-positive `repetitions`, or when the resolved `workflow_version` does not exist.

#### Scenario: Create workflow answer-source task with explicit version
- **WHEN** `POST /api/evaluation/tasks` is called with `{"task_type": "offline", "name": "wf-regress", "dataset_id": "...", "evaluators": ["correctness"], "answer_source": "workflow", "workflow_id": "...", "workflow_version": 7, "repetitions": 3}`
- **THEN** the task SHALL be persisted with status `active`, the resolved workflow version locked, and the runnable configuration SHALL expose `workflow_id`, `workflow_version=7`, and `repetitions=3`

#### Scenario: Create workflow answer-source task without version
- **WHEN** the task is created with `workflow_id` but no `workflow_version`
- **THEN** the task SHALL be persisted and the version SHALL be to be the workflow's current latest version at the moment the next run starts

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

The snapshot SHALL additionally carry each item's known-bad marker (the marker fields travel on the snapshot item only when the item is marked), and run aggregation SHALL read exemption state from the snapshot, never from the live dataset at aggregation time. The content `hash` SHALL be computed over the evaluation-content fields only (`query`, `expected_answer`, `context`, `tags`, `metadata`) — marking or clearing an exemption SHALL NOT change the hash and SHALL NOT surface as `dataset_drift`.

#### Scenario: Snapshot captured at run start
- **WHEN** a run starts against a dataset with 10 items
- **THEN** the run record SHALL carry a `dataset_snapshot` with the items' canonical JSON and a hash, persisted before any item executes; items marked known-bad carry their marker on the snapshot item, unmarked items serialize without any marker keys

#### Scenario: Snapshot hash compared against current dataset hash
- **WHEN** the run completes and the dataset has changed during the run (items added/removed/edited)
- **THEN** the run's `summary` SHALL include `dataset_snapshot_hash`, `dataset_current_hash`, and (when they differ) `dataset_drift_item_ids`; when an item was merely marked or unmarked as known-bad after the snapshot was taken, the comparison SHALL report no `dataset_drift` (exemption changes do not affect the content hash)

#### Scenario: Diff uses snapshot, not live dataset
- **WHEN** `POST /api/evaluation/runs/compare` is called with two runs whose snapshots differ
- **THEN** the response SHALL align items by snapshot position, not by current dataset ordering, and SHALL report `dataset_drift` describing the hash mismatch

#### Scenario: Aggregation reads snapshot-time exemption state
- **WHEN** an item is marked known-bad after a run has completed
- **THEN** the completed run's summary SHALL remain based on the snapshot-time state (unchanged), and only runs started after the marking SHALL exclude the item

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
- **THEN** the API SHALL return 422 with a clear error explaining the alignment failure rather than to return a misleading diff

### Requirement: Repetition aggregation in run summary

When `repetitions > 1`, the run's `summary` SHALL include, for each metric: `metric_avg` (mean across all repetitions), `pass_rate` (fraction of repetitions where the metric meets its threshold), and `consistency_rate` (fraction of items where every repetition meets the threshold). When `repetitions = 1`, the summary SHALL include `pass_rate` only. `consistency_rate` SHALL NOT be exposed when `repetitions = 1`. When a pass/fail threshold is configured for the run, the summary SHALL additionally include the effective `threshold` value, so that downstream consumers (including the publish gate) can recompute item-level pass semantics from the recorded scores.

#### Scenario: Single-repetition run summary

- **WHEN** a run with `repetitions: 1` completes
- **THEN** the summary SHALL include `pass_rate` and `metric_averages` exactly as in 7.2c behavior, and SHALL NOT include `consistency_rate`

#### Scenario: Multi-repetition run summary

- **WHEN** a run with `repetitions: 3` completes and the same item meets the threshold in repetitions 1 and 2 but fails in repetition 3
- **THEN** the item's `pass_rate` SHALL be `0.67` (2 of 3) and the item SHALL NOT contribute to `consistency_rate` (since consistency requires every repetition to pass)

#### Scenario: Effective threshold persisted in summary

- **WHEN** a run completes with a configured threshold of `0.8`
- **THEN** the summary SHALL include `threshold: 0.8`; when no threshold is configured, the summary SHALL NOT include a `threshold` key
### Requirement: Publish-time evaluation report

The system SHALL, on `POST /workflows/{workflow_id}/publish/{version}`, attach an `evaluation_report` field to the response when at least one workflow evaluation run exists for the publishing version. The report SHALL contain: the latest run's `id`, `pass_rate`, `consistency_rate` (when `repetitions > 1`), and a `comparison_to_published` block containing per-metric deltas against the most recent run of the currently-published version (if such a run exists). The report SHALL additionally include a `gate` block — the publish evaluation gate's mode and per-signal verdicts for this publish — whenever a gate is configured on the workflow, and a `dataset_version` block (version id, name, and content hash) whenever the evaluated run was bound to a named dataset version. The publish API SHALL remain non-blocking when the gate is disabled or in `warn` mode: the report is informational, missing runs are surfaced as `evaluation_status: "no_baseline"`, and the publish response SHALL NOT be rejected due to missing or weak evaluation data. Rejection behavior when the gate is in `require` mode is governed by the publish evaluation gate requirement in the `workflow-version-publish` capability.

#### Scenario: Publish with comparison against previously published version

- **WHEN** `POST /workflows/{workflow_id}/publish/{version}` is called and the publishing version has an evaluation run while the currently published version also has a recent run
- **THEN** the response SHALL include `evaluation_report` with the new run's summary and `comparison_to_published` containing per-metric deltas

#### Scenario: Publish with no prior baseline

- **WHEN** `POST /workflows/{workflow_id}/publish/{version}` is called and no workflow evaluation run exists for the publishing version
- **THEN** the response SHALL include `evaluation_report: null` and the publish SHALL succeed normally

#### Scenario: Publish with regression while gate is disabled or warn

- **WHEN** the publishing version's run shows a regressed metric versus the currently published version and the gate is disabled or in `warn` mode
- **THEN** the response SHALL return 200 OK and the `evaluation_report` SHALL describe the regression; the system SHALL NOT block the publish based on the evaluation data

#### Scenario: Report carries gate and dataset version blocks

- **WHEN** a publish response includes an evaluation report for a run that was bound to named dataset version `v3.0`, and the workflow has a gate configured in `warn` mode
- **THEN** the report SHALL include `gate` with `mode: "warn"` and the per-signal verdicts, and `dataset_version` with the version's id, name, and content hash
### Requirement: Run cost guardrails
The system SHALL reject triggering an offline workflow-evaluation run when `items × repetitions` exceeds the task's `max_total_executions` (positive integer, default 1000) with a 400 error naming the violation. The system SHALL also cap the in-flight concurrency in flight of workflow invocations within a single run at the task's `max_in_flight` (positive integer, default 4); executions beyond the cap SHALL queue rather than run in parallel.

#### Scenario: Reject run exceeding total execution cap
- **WHEN** a task with 600 items and `repetitions: 2` is triggered (total 1200) and `max_total_executions` is the default 1000
- **THEN** the trigger API SHALL return 400 with a message identifying the cap and the offending total

#### Scenario: Concurrency cap respected within a run
- **WHEN** a run is executing with `max_in_flight: 2` and 10 items are queued
- **THEN** at most 2 workflow invocations SHALL be in flight at any time; the remaining items SHALL be processed as slots free up

### Requirement: CLI eval run with three-state exit code

The system SHALL provide a `hecate workflow eval run` CLI subcommand accepting `--dataset` (dataset id or name), `--workflow-id`, `--workflow-version` (optional), `--repetitions` (optional, defaults to task default), `--baseline-run-id` (optional), and `--dataset-version-id` (optional). When `--dataset-version-id` is provided, the triggered run SHALL be bound to that named dataset version per the version-bound evaluation runs requirement. On completion, the CLI SHALL print a JSON result to stdout and exit with code 0 when no regression is detected and the dataset snapshot has not drifted; exit code 2 when the snapshot has drifted (warning, not failure); and exit code 3 when at least one metric regression is detected. The CLI SHALL NOT post PR comments; report rendering is left to the calling CI system.

#### Scenario: CLI run exits 0 on success

- **WHEN** the CLI run completes with all metrics within threshold and no dataset drift
- **THEN** the exit code SHALL be 0 and the stdout JSON SHALL include `passed: true`

#### Scenario: CLI run exits 2 on dataset drift

- **WHEN** the dataset snapshot hash differs from the dataset's current hash but no metric regression occurred
- **THEN** the exit code SHALL be 2 and the JSON SHALL include `dataset_drift: {old_hash, new_hash, changed_item_ids}` with `passed: true` and a `warnings` array

#### Scenario: CLI run exits 3 on regression

- **WHEN** at least one metric drops more than its configured threshold versus the baseline
- **THEN** the exit code SHALL be 3 and the JSON SHALL include `regressions: [{metric, baseline_avg, candidate_avg, delta}]` with `passed: false`

#### Scenario: CLI run bound to a named dataset version

- **WHEN** `hecate workflow eval run` is called with `--dataset-version-id` pointing at an existing version
- **THEN** the triggered run SHALL execute the version's frozen items, the stdout JSON SHALL identify the bound version, and the three-state exit code semantics SHALL apply unchanged
### Requirement: Known-bad exemption in run aggregation
The system SHALL exclude known-bad items from run quality aggregation while still executing them and recording their scores. Known-bad items SHALL NOT contribute to the numerator or the denominator of `pass_rate`, SHALL NOT contribute to `consistency_rate` (when `repetitions > 1`), SHALL NOT contribute to `metric_averages`, and SHALL NOT contribute to the per-metric averages used for regression determination (`is_regression`). The run summary SHALL include an `exempted_items` count of snapshot items excluded by this requirement. When a known-bad item's repetitions all meet their metric threshold, the summary SHALL list the item's id in `known_bad_passed_item_ids` as an explicit "dataset healed" signal; the system SHALL NOT automatically clear the exemption. The three-state CLI exit code semantics SHALL operate on the excluded-aggregation `pass_rate` without change.

#### Scenario: Exempted item removed from pass_rate denominator
- **WHEN** a run executes a dataset of 10 items of which 2 are marked known-bad in the snapshot, and 6 of the 8 active items pass
- **THEN** `pass_rate` SHALL be `0.75` (6 of 8), the summary SHALL report `exempted_items: 2`, and all 10 items SHALL still have scores recorded

#### Scenario: Exempted item passing is surfaced, not auto-cleared
- **WHEN** a known-bad item's repetitions all meet their threshold in a run
- **THEN** the item's id SHALL appear in `summary.known_bad_passed_item_ids`, the item SHALL remain known-bad after the run, and `pass_rate` SHALL NOT count it

#### Scenario: Exempted items excluded from regression averages
- **WHEN** `POST /api/evaluation/runs/compare` compares a baseline and a candidate run that both contain known-bad items
- **THEN** per-metric `baseline_avg`, `candidate_avg`, and `is_regression` SHALL be computed over active items only, for both runs

#### Scenario: Single-repetition summary with exemptions
- **WHEN** a run with `repetitions = 1` executes a dataset containing known-bad items
- **THEN** the summary SHALL include `pass_rate` and `exempted_items`, and SHALL NOT include `consistency_rate`

### Requirement: Version-bound evaluation runs

The workflow evaluation trigger SHALL accept an optional `dataset_version_id`. When provided and the version exists and belongs to the request's workspace, the resulting run SHALL be bound to that version: the run's `dataset_snapshot` SHALL be taken verbatim from the version's frozen items and content hash (with the version's id and name recorded in the snapshot), the run SHALL execute the version's items rather than the live dataset's items, the pre-flight cost guardrail SHALL count the version's items, and the run SHALL expose `dataset_version_id`. Live dataset edits SHALL NOT affect an already-triggered version-bound run. Triggering with a non-existent or foreign-workspace version SHALL be rejected. Runs without `dataset_version_id` SHALL keep the existing behavior unchanged (live freeze at run start, post-run drift comparison).

#### Scenario: Bound run executes frozen items regardless of live edits

- **WHEN** a run is triggered with `dataset_version_id` of a version frozen with 10 items, and the live dataset is edited while the run executes
- **THEN** the run SHALL execute exactly the version's 10 items, its snapshot SHALL carry the version's id, name, and content hash, and no live edit SHALL appear in its results

#### Scenario: Guardrail counts version items

- **WHEN** a version-bound run is triggered with `repetitions` such that `version_items × repetitions` is within the cost guardrail while `live_items × repetitions` would exceed it
- **THEN** the trigger SHALL be accepted (the guardrail counts the items that will actually execute)

#### Scenario: Unbound run behavior unchanged

- **WHEN** a run is triggered without `dataset_version_id`
- **THEN** the run SHALL freeze the live dataset at run start, execute the live items, and report `dataset_drift` when the live content hash changes before completion — identical to pre-versioning behavior

#### Scenario: Foreign or unknown version rejected

- **WHEN** a trigger supplies a `dataset_version_id` that does not exist or belongs to another workspace
- **THEN** the trigger SHALL be rejected and no run SHALL be created
