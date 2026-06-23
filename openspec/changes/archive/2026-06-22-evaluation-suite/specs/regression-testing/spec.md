## ADDED Requirements

### Requirement: Dataset versioning
The system SHALL support dataset versioning via three new fields on `EvaluationDatasetModel`: `version` (String, default "v1.0"), `baseline_run_id` (UUID FK to EvaluationRunModel, nullable), and `is_locked` (Boolean, default False). When a dataset is locked, item additions, modifications, and deletions SHALL be rejected.

#### Scenario: Set baseline run for dataset
- **WHEN** a user sets `baseline_run_id` on a dataset after a successful evaluation run
- **THEN** subsequent regression runs SHALL compare their scores against this baseline run

#### Scenario: Lock a golden dataset
- **WHEN** a user locks a dataset with `is_locked=True`
- **THEN** attempts to add, modify, or delete items SHALL return 409 Conflict

#### Scenario: Version tag for dataset
- **WHEN** a dataset is created with `version="v2.0"`
- **THEN** the version tag SHALL be stored and returned in dataset read responses

### Requirement: Per-item assertion model
The system SHALL support per-item assertions via an `assertions` JSON field on `EvaluationItemModel`. Each assertion SHALL have a `type` (evaluator name or deterministic check), an optional `threshold` (float), and optional `value` (for deterministic checks). Items without assertions SHALL inherit dataset-level defaults.

#### Scenario: Item with assertion overrides
- **WHEN** an item has `assertions=[{"type": "faithfulness", "threshold": 0.9}]`
- **THEN** the evaluation engine SHALL apply threshold 0.9 for faithfulness on this item, regardless of dataset default

#### Scenario: Item with deterministic assertion
- **WHEN** an item has `assertions=[{"type": "contains", "value": "RAG"}]`
- **THEN** the engine SHALL check if `generated_answer` contains "RAG" and mark pass/fail without calling an evaluator

#### Scenario: Item without assertions inherits dataset default
- **WHEN** an item has no assertions and the dataset has `default_threshold=0.7`
- **THEN** all evaluator scores for this item SHALL use 0.7 as the pass threshold

### Requirement: Dataset default threshold
The system SHALL support a `default_threshold` Float field on `EvaluationDatasetModel` (nullable, default None). When set, all items without explicit assertions SHALL use this threshold for pass/fail evaluation.

#### Scenario: Dataset with default threshold
- **WHEN** a dataset has `default_threshold=0.75` and items without explicit assertions
- **THEN** each evaluator score >= 0.75 on those items SHALL be marked as passed

### Requirement: Item tags for grouping
The system SHALL support a `tags` JSON field (array of strings) on `EvaluationItemModel` for categorizing test cases. Tags enable filtered evaluation runs (e.g., only run "smoke" tests, or only "regression" tests).

#### Scenario: Filter evaluation run by tags
- **WHEN** an evaluation run is created with `tags=["smoke"]`
- **THEN** only items with "smoke" in their tags array SHALL be evaluated

#### Scenario: Item with multiple tags
- **WHEN** an item has `tags=["smoke", "regression", "edge_case"]`
- **THEN** it SHALL be included in runs filtered by any of those tags

### Requirement: Run comparison API
The system SHALL expose `POST /api/evaluation/runs/compare` that accepts `baseline_run_id` and `candidate_run_id`, computes per-metric score deltas, and flags regressions where score drops exceed a configurable threshold (default 5%).

#### Scenario: Compare two runs with regression
- **WHEN** a comparison is made between baseline (faithfulness avg=0.85) and candidate (faithfulness avg=0.72)
- **THEN** the response SHALL include `{"metric": "faithfulness", "baseline_avg": 0.85, "candidate_avg": 0.72, "delta": -0.13, "is_regression": true}`

#### Scenario: Compare two runs without regression
- **WHEN** a comparison is made and all metric deltas are within ±5%
- **THEN** the response SHALL include `"overall_regressed": false` and no items in the regressions array

### Requirement: Per-item pass/fail computation
The system SHALL compute per-item pass/fail results during evaluation runs. An item passes if ALL of its assertions (or dataset default threshold) are met. The pass/fail result SHALL be persisted in `EvaluationScoreModel` or a derived computation from scores + assertions.

#### Scenario: Item passes all assertions
- **WHEN** an item has assertions for faithfulness (threshold 0.8) and correctness (threshold 0.7), and both scores meet thresholds
- **THEN** the item SHALL be marked as passed

#### Scenario: Item fails one assertion
- **WHEN** an item has assertions for faithfulness (threshold 0.8, actual 0.6) and correctness (threshold 0.7, actual 0.9)
- **THEN** the item SHALL be marked as failed with the failing assertion identified

### Requirement: Regression trigger API for CI/CD
The system SHALL expose `POST /api/evaluation/regression/run` that accepts `dataset_id`, `evaluators` list, optional `tags` filter, optional `threshold` override, and optional `baseline_run_id`. It SHALL execute the evaluation, compare against baseline, and return a structured pass/fail report in a single response.

#### Scenario: Successful regression run
- **WHEN** a CI/CD pipeline calls the regression endpoint with a dataset and evaluator list
- **THEN** the response SHALL include `passed: bool`, `total_items`, `passed_items`, `failed_items`, `regressions` array, and `metric_averages`

#### Scenario: Regression run with tag filter
- **WHEN** the regression endpoint is called with `tags=["smoke"]`
- **THEN** only items tagged "smoke" SHALL be evaluated, reducing execution time for CI fast-feedback

#### Scenario: Regression detected blocks CI
- **WHEN** the regression run detects score regressions exceeding the threshold
- **THEN** `passed` SHALL be `false` and the response SHALL include details of each regression for CI to fail the build

### Requirement: Run summary with pass/fail statistics
The system SHALL include pass/fail statistics in the `GET /api/evaluation/runs/{run_id}` response: `total_items`, `passed_items`, `failed_items`, and `pass_rate`.

#### Scenario: Get run with pass/fail summary
- **WHEN** `GET /api/evaluation/runs/{run_id}` is called for a completed run
- **THEN** the response SHALL include `pass_rate` computed as `passed_items / total_items`
