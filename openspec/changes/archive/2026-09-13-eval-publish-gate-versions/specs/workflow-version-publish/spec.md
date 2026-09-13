# Delta: workflow-version-publish

## ADDED Requirements

### Requirement: Publish evaluation gate

The system SHALL support a per-workflow publish evaluation gate stored on the workflow as an `evaluation_gate` configuration, where absent/null means the gate is disabled and publish behavior remains byte-identical to the pre-gate contract. When present, the configuration SHALL carry: `mode` (`warn` or `require`), `min_pass_rate` (optional float in [0, 1]), `block_on_regression` (bool), `block_on_drift` (bool), `require_run` (bool), and `require_dataset_version` (bool). The workflow update API SHALL reject a configuration with `mode=require` in which no signal is enabled, and SHALL reject `min_pass_rate` values outside [0, 1].

The gate SHALL evaluate the latest completed workflow evaluation run of the publishing version using deterministic signals only: the run's recorded scores with source `deterministic` SHALL be recomputed into a per-item deterministic pass rate (an item passes when all of its deterministic scores meet the run's threshold) and per-metric deterministic averages; LLM-judge and human scores SHALL NEVER participate in gate blocking. The gate signals SHALL be: deterministic pass rate below `min_pass_rate` (when set); any deterministic metric regressed versus the most recent completed run of the currently published version (when `block_on_regression`); the run's snapshot hash differs from the dataset's current content hash (when `block_on_drift`); no completed run exists for the publishing version (when `require_run`); the run is not bound to a named dataset version (when `require_dataset_version`); and the run carries no deterministic scores at all (unsatisfied under `mode=require`, reported as `no_deterministic_scores`).

When `mode=warn`, publish SHALL always succeed and the `evaluation_report` SHALL carry the gate result including any unsatisfied signals. When `mode=require` and any enabled signal is unsatisfied, publish SHALL be rejected with HTTP 409 and error code `EVALUATION_GATE_BLOCKED`; the response body details SHALL include the full gate result and the complete evaluation report so clients need no follow-up request to display the reason. The publish endpoint SHALL accept an optional JSON body `{"force": true}`; when forced, publish SHALL succeed regardless of gate state, the report SHALL mark `gate.bypassed_by_force: true`, and the bypass SHALL be recorded in the audit log with the acting user.

#### Scenario: Gate disabled preserves the existing contract

- **WHEN** a workflow has no `evaluation_gate` configuration and `POST /workflows/{workflow_id}/publish/{version}` is called
- **THEN** the response SHALL be identical to the pre-gate contract (200, with the informational evaluation report)

#### Scenario: Require mode blocks on low deterministic pass rate

- **WHEN** the gate is `mode=require` with `min_pass_rate: 0.9`, the publishing version's latest run has a deterministic pass rate of `0.7`, and publish is called without `force`
- **THEN** the response SHALL be 409 with code `EVALUATION_GATE_BLOCKED`, and the details SHALL contain the gate result and the full evaluation report

#### Scenario: Require mode blocks on deterministic metric regression

- **WHEN** the gate is `mode=require` with `block_on_regression: true` and a deterministic metric of the publishing version's run regressed versus the currently published version's most recent run
- **THEN** publish SHALL be rejected with 409 `EVALUATION_GATE_BLOCKED`

#### Scenario: LLM-judge signals never block

- **WHEN** the gate is `mode=require` and the publishing version's run shows a regression only in an LLM-judge metric while all deterministic signals are satisfied
- **THEN** publish SHALL succeed with 200 and the report SHALL describe the LLM-judge regression without a gate failure

#### Scenario: Run without deterministic scores cannot satisfy a require gate

- **WHEN** the gate is `mode=require` and the publishing version's only completed run used exclusively LLM-judge evaluators
- **THEN** publish SHALL be rejected with 409 and the gate result SHALL report `no_deterministic_scores`

#### Scenario: Warn mode reports without blocking

- **WHEN** the gate is `mode=warn` and the same failing signals as a blocking case are present
- **THEN** publish SHALL return 200, the workflow SHALL be published, and `evaluation_report.gate` SHALL list the unsatisfied signals

#### Scenario: Force bypasses and is audited

- **WHEN** publish is called with body `{"force": true}` while the require gate would block
- **THEN** the response SHALL be 200, the version SHALL be published, `evaluation_report.gate.bypassed_by_force` SHALL be `true`, and an audit entry SHALL record the bypass and the acting user

#### Scenario: Require dataset version signal

- **WHEN** the gate is `mode=require` with `require_dataset_version: true` and the publishing version's latest run is not bound to a named dataset version
- **THEN** publish SHALL be rejected with 409; when a later run bound to a named version completes, publish SHALL pass this signal

#### Scenario: Gate configuration validation

- **WHEN** the workflow update API receives `evaluation_gate` with `mode=require` and every signal disabled/null, or with `min_pass_rate: 1.5`
- **THEN** the update SHALL be rejected with a validation error and the stored configuration SHALL remain unchanged
