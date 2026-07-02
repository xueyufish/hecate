## ADDED Requirements

### Requirement: Evaluator listing API
The system SHALL expose `GET /api/evaluation/evaluators` that returns all registered evaluators with their name, description, category, source type (deterministic/llm_judge), and required input fields. Supports optional `category` query parameter for filtering.

#### Scenario: List all evaluators
- **WHEN** `GET /api/evaluation/evaluators` is called
- **THEN** all 41 registered evaluators are returned with name, description, category, and source_type

#### Scenario: List evaluators by category
- **WHEN** `GET /api/evaluation/evaluators?category=process` is called
- **THEN** only evaluators in the "process" category are returned

### Requirement: Run comparison API
The system SHALL expose `POST /api/evaluation/runs/compare` that accepts `baseline_run_id` and `candidate_run_id`, returns per-metric deltas, per-item pass/fail changes, and regression flags.

#### Scenario: Compare two runs
- **WHEN** `POST /api/evaluation/runs/compare` is called with valid baseline and candidate run IDs
- **THEN** the response SHALL include per-metric averages for both runs, deltas, and regression flags for metrics where the candidate score dropped more than the threshold (default 5%)

### Requirement: Regression trigger API
The system SHALL expose `POST /api/evaluation/regression/run` that accepts `dataset_id`, `evaluators`, optional `tags`, optional `threshold`, and optional `baseline_run_id`. It SHALL execute the evaluation run, compare against baseline if provided, compute pass/fail per item, and return a structured regression report.

#### Scenario: Trigger regression run
- **WHEN** `POST /api/evaluation/regression/run` is called with a dataset ID and evaluator list
- **THEN** the response SHALL include `run_id`, `passed`, `total_items`, `passed_items`, `failed_items`, `regressions`, and `metric_averages`

## MODIFIED Requirements

### Requirement: Evaluation run API
The system SHALL expose REST endpoints at `/api/evaluation/runs` for creating and retrieving evaluation runs. Runs SHALL support optional `tags` parameter for tag-filtered evaluation. The run response SHALL include pass/fail statistics when assertions or thresholds are configured.

#### Scenario: Create evaluation run
- **WHEN** a POST request is sent to `/api/evaluation/runs` with `{"dataset_id": "...", "evaluators": ["faithfulness", "context_precision"]}`
- **THEN** the API SHALL execute the specified evaluators against the dataset items, persist the run with all scores, and return 201 with the `EvaluationRunResult`

#### Scenario: Create tag-filtered evaluation run
- **WHEN** a POST request is sent to `/api/evaluation/runs` with `{"dataset_id": "...", "evaluators": [...], "tags": ["smoke"]}`
- **THEN** only items tagged "smoke" SHALL be evaluated

#### Scenario: Get run with pass/fail summary
- **WHEN** a GET request is sent to `/api/evaluation/runs/{id}`
- **THEN** the response SHALL include `total_items`, `passed_items`, `failed_items`, `pass_rate`, and `metric_averages`

#### Scenario: Get run scores
- **WHEN** a GET request is sent to `/api/evaluation/runs/{id}/scores`
- **THEN** the API SHALL return all individual scores for the run, grouped by evaluator metric
