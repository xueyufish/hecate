# Delta: workflow-evaluation

## MODIFIED Requirements

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

## ADDED Requirements

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
