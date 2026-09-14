## Purpose
Define workflow version publishing and diff capabilities — production pointer, deployment labels, and version comparison.

## Requirements

### Requirement: Workflow published version pointer
The `WorkflowModel` SHALL include a `published_version` field of type `int | None`, defaulting to `None`. When a version is published, this field SHALL be set to the published version number. Only one version per workflow SHALL be the published version at any time.

#### Scenario: Initial workflow has no published version
- **WHEN** a new workflow is created with version 1
- **THEN** `published_version` SHALL be `None`

#### Scenario: Publish a specific version
- **WHEN** `publish_version(workflow_id, version=3)` is called
- **THEN** `published_version` SHALL be set to `3`
- **AND** an audit log entry with action `WORKFLOW_VERSION_PUBLISH` SHALL be created

#### Scenario: Republish overwrites previous published version
- **WHEN** `published_version` is `3` and `publish_version(workflow_id, version=5)` is called
- **THEN** `published_version` SHALL be updated to `5`
- **AND** the previous published version 3 SHALL remain in the version history unchanged

#### Scenario: Publish non-existent version
- **WHEN** `publish_version(workflow_id, version=99)` is called and version 99 does not exist
- **THEN** the service SHALL raise `ValueError`

### Requirement: Workflow version deployment labels
The `WorkflowVersionModel` SHALL include a `labels` field of type `list[str]`, defaulting to an empty list. Labels SHALL follow the pattern established by `PromptVersionModel.labels`. Standard labels SHALL include `"production"`, `"staging"`, and `"development"`.

#### Scenario: Version created with labels
- **WHEN** a workflow version is created with `labels=["staging"]`
- **THEN** the version SHALL store those labels

#### Scenario: Publish sets production label
- **WHEN** `publish_version(workflow_id, version=3)` is called
- **THEN** version 3 SHALL have `"production"` added to its labels
- **AND** any previously published version SHALL have `"production"` removed from its labels

#### Scenario: Query by label
- **WHEN** `get_version_by_label(workflow_id, label="production")` is called
- **THEN** the service SHALL return the version with `"production"` in its labels

### Requirement: Workflow version diff comparison
The `WorkflowService` SHALL provide a `diff_versions(workflow_id, v1, v2)` method that compares two workflow versions' `graph_dsl` fields and returns a structured diff result. The result SHALL categorize changes into: nodes added, nodes removed, nodes modified, edges added, edges removed, edges modified, state changes.

#### Scenario: Diff between versions with node changes
- **WHEN** `diff_versions(workflow_id, v1=1, v2=2)` is called where version 2 added a "validator" node and removed the "checker" node
- **THEN** the result SHALL contain `{"nodes_added": ["validator"], "nodes_removed": ["checker"], "nodes_modified": [], "edges_added": [], "edges_removed": [], "edges_modified": [], "state_changes": []}`

#### Scenario: Diff between identical versions
- **WHEN** `diff_versions(workflow_id, v1=1, v2=1)` is called
- **THEN** the result SHALL contain empty lists for all change categories

#### Scenario: Diff with non-existent version
- **WHEN** `diff_versions(workflow_id, v1=1, v2=99)` is called and version 99 does not exist
- **THEN** the service SHALL raise `ValueError`

### Requirement: Publish and diff API endpoints
The workflow management API SHALL expose publish and diff endpoints. `POST /api/workflows/{id}/publish/{version}` SHALL publish a specific version. `GET /api/workflows/{id}/diff?v1={v1}&v2={v2}` SHALL return the diff between two versions. `GET /api/workflows/{id}/published` SHALL return the currently published version.

#### Scenario: Publish via API
- **WHEN** `POST /api/workflows/{id}/publish/3` is called
- **THEN** the response SHALL be `{"published_version": 3}` with status 200

#### Scenario: Diff via API
- **WHEN** `GET /api/workflows/{id}/diff?v1=1&v2=2` is called
- **THEN** the response SHALL contain the structured diff result with status 200

#### Scenario: Get published version via API
- **WHEN** `GET /api/workflows/{id}/published` is called and `published_version=3`
- **THEN** the response SHALL return the full version 3 data with status 200

#### Scenario: Get published version when none published
- **WHEN** `GET /api/workflows/{id}/published` is called and `published_version` is `None`
- **THEN** the response SHALL be `{"error": {"code": "NOT_PUBLISHED", "message": "No published version"}}` with status 404

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

### Requirement: 运行时按执行上下文解析工作流版本

工作流执行 SHALL 按执行上下文解析所用版本：studio 编辑器内的试跑 SHALL 使用最新草稿版本；其余执行路径 SHALL 优先使用 `published_version`，该工作流从未发布过时 SHALL 回落使用最新版本（保持存量行为）。版本一经解析，本次执行的整条图 SHALL 使用同一版本的定义。

#### Scenario: 已发布工作流执行已发布版

- **WHEN** 工作流 W 的 published_version=7、最新版本=9（草稿），非 studio 试跑路径触发 W 的执行
- **THEN** 执行 SHALL 加载版本 7 的图定义，SHALL NOT 使用版本 9

#### Scenario: 从未发布的工作流回落最新版

- **WHEN** 工作流 W 从未发布（published_version 为空），触发执行
- **THEN** 执行 SHALL 使用最新版本，行为与存量一致

#### Scenario: studio 试跑走草稿

- **WHEN** 在 studio 编辑器内对 W（published_version=7、最新版本=9）发起试跑
- **THEN** 试跑 SHALL 使用版本 9（最新草稿）

#### Scenario: 版本解析单次执行内一致

- **WHEN** 一次执行解析到版本 7 后，另一次发布将 published_version 改为 8
- **THEN** 进行中的那次执行 SHALL 全程使用版本 7 的定义
