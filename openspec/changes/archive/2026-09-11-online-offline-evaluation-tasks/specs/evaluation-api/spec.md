## MODIFIED Requirements

### Requirement: Evaluation run API
The system SHALL expose REST endpoints at `/api/evaluation/runs` for creating and retrieving evaluation runs. Runs SHALL support optional `tags` parameter for tag-filtered evaluation. Runs MAY be linked to an evaluation task via a nullable `task_id`; the run response SHALL include `task_id` (null for request-triggered runs) and a `summary` object (`total_items`, `passed_items`, `failed_items`, `pass_rate`, `metric_averages`, `regressions`) when pass/fail statistics have been computed. The run listing endpoint SHALL support an optional `task_id` query filter.

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

#### Scenario: List runs filtered by task
- **WHEN** a GET request is sent to `/api/evaluation/runs?task_id={task_id}`
- **THEN** only runs linked to that evaluation task SHALL be returned
