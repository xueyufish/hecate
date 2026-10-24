## Purpose

Defines the evaluation APIs: evaluator listing, run execution, run comparison, and regression triggering.

## Requirements

### Requirement: Evaluator listing API
The system SHALL expose `GET /api/evaluation/evaluators` that returns all registered evaluators with their name, description, category, source type (deterministic/llm_judge), and required input fields. Supports optional `category` query parameter for filtering.

#### Scenario: List all evaluators
- **WHEN** `GET /api/evaluation/evaluators` is called
- **THEN** all 41 registered evaluators are returned with name, description, category, and source_type

#### Scenario: List evaluators by category
- **WHEN** `GET /api/evaluation/evaluators?category=process` is called
- **THEN** only evaluators in the "process" category are returned

### Requirement: Run comparison API
The system SHALL expose `POST /api/evaluation/runs/compare` that accepts `baseline_run_id` and `candidate_run_id`, returns per-metric deltas, per-item pass/fail changes, and regression flags. Before computing a comparison, the API SHALL validate that both the baseline and candidate runs satisfy the evaluation run read access boundary (existence, not soft-deleted, caller's workspace, viewer role). An invisible run SHALL produce a 404 response whose body is identical regardless of which condition applies and which run failed validation, and the body SHALL NOT echo run identifiers.

#### Scenario: Compare two runs
- **WHEN** `POST /api/evaluation/runs/compare` is called by a viewer (or higher) of their workspace with valid baseline and candidate run IDs from that workspace
- **THEN** the response SHALL include per-metric averages for both runs, deltas, and regression flags for metrics where the candidate score dropped more than the threshold (default 5%)

#### Scenario: Comparison with invisible run returns uniform not-found
- **WHEN** the compare request references a run that does not exist, is soft-deleted, or belongs to another workspace
- **THEN** the response SHALL be 404 with one consistent body across all conditions, without echoing the run ID or distinguishing the conditions; no comparison SHALL be computed

#### Scenario: Comparison requires viewer role
- **WHEN** an authenticated identity whose active workspace contains both runs but whose workspace membership role is below viewer (or absent) calls the compare endpoint
- **THEN** the response SHALL be 403 authorization denied and no comparison SHALL be computed

### Requirement: Regression trigger API
The system SHALL expose `POST /api/evaluation/regression/run` that accepts `dataset_id`, `evaluators`, optional `tags`, optional `threshold`, and optional `baseline_run_id`. It SHALL execute the evaluation run, compare against baseline if provided, compute pass/fail per item, and return a structured regression report.

#### Scenario: Trigger regression run
- **WHEN** `POST /api/evaluation/regression/run` is called with a dataset ID and evaluator list
- **THEN** the response SHALL include `run_id`, `passed`, `total_items`, `passed_items`, `failed_items`, `regressions`, and `metric_averages`


### Requirement: Evaluation run API
The system SHALL expose REST endpoints at `/api/evaluation/runs` for creating and retrieving evaluation runs. Runs SHALL support optional `tags` parameter for tag-filtered evaluation. Runs MAY be linked to an evaluation task via a nullable `task_id`; the run response SHALL include `task_id` (null for request-triggered runs) and a `summary` object (`total_items`, `passed_items`, `failed_items`, `pass_rate`, `metric_averages`, `regressions`) when pass/fail statistics have been computed. The run listing endpoint SHALL support an optional `task_id` query filter.

Run listing, detail, and scores SHALL enforce the evaluation run read access boundary: a non-system identity SHALL only observe runs in its active workspace, and the listing `total` SHALL count only runs within the caller's workspace. A run that does not exist, is soft-deleted, or belongs to another workspace SHALL produce the same not-found (404) response for a given endpoint, and the response SHALL NOT disclose which condition applied. `GET /api/evaluation/runs/{run_id}/scores` SHALL validate run visibility before reading scores; an invisible run SHALL return 404 instead of an empty score list.

#### Scenario: Create evaluation run
- **WHEN** a POST request is sent to `/api/evaluation/runs` with `{"dataset_id": "...", "evaluators": ["faithfulness", "context_precision"]}`
- **THEN** the API SHALL execute the specified evaluators against the dataset items, persist the run with all scores, and return 201 with the `EvaluationRunResult`

#### Scenario: Create tag-filtered evaluation run
- **WHEN** a POST request is sent to `/api/evaluation/runs` with `{"dataset_id": "...", "evaluators": [...], "tags": ["smoke"]}`
- **THEN** only items tagged "smoke" SHALL be evaluated

#### Scenario: Get run with pass/fail summary
- **WHEN** a GET request is sent to `/api/evaluation/runs/{id}` by a viewer (or higher) of the run's workspace
- **THEN** the response SHALL include `total_items`, `passed_items`, `failed_items`, `pass_rate`, and `metric_averages`

#### Scenario: Get run scores
- **WHEN** a GET request is sent to `/api/evaluation/runs/{id}/scores` by a viewer (or higher) of the run's workspace
- **THEN** the API SHALL return the individual scores for the run, grouped by evaluator metric

#### Scenario: Scores for invisible run return not-found
- **WHEN** a GET request is sent to `/api/evaluation/runs/{id}/scores` for a run that does not exist, is soft-deleted, or belongs to another workspace
- **THEN** the response SHALL be 404 with the same body for all three conditions; the API SHALL NOT return an empty score list that would reveal whether the run exists elsewhere

#### Scenario: Listing is scoped to the caller workspace
- **WHEN** runs exist in workspaces W1 and W2 and a W1 member calls `GET /api/evaluation/runs`
- **THEN** only W1 runs are returned and `total` counts only W1 runs

#### Scenario: Cross-workspace filter values do not escalate visibility
- **WHEN** a W1 member calls `GET /api/evaluation/runs` with a `dataset_id` or `task_id` that belongs to workspace W2
- **THEN** the response SHALL be an empty item list with `total` 0, and no W2 run metadata SHALL be returned

#### Scenario: Detail for invisible run is indistinguishable from missing
- **WHEN** a W1 member requests `/api/evaluation/runs/{id}` for a run that is soft-deleted or belongs to W2, and for a run ID that does not exist at all
- **THEN** all three requests SHALL receive the same 404 response body

#### Scenario: List runs filtered by task
- **WHEN** a GET request is sent to `/api/evaluation/runs?task_id={task_id}` for a task in the caller's workspace
- **THEN** only runs linked to that evaluation task SHALL be returned

### Requirement: Evaluation run read access boundary

The evaluation run listing, detail, scores, and comparison endpoints SHALL enforce one shared read access boundary: an authenticated non-system identity SHALL only access evaluation runs belonging to its active workspace, with a minimum viewer role in that workspace; explicit system-scope API keys SHALL read across workspaces; a non-system identity without an active workspace SHALL be denied and SHALL NOT degrade to a global query. Ownership SHALL be checked before role so that a cross-workspace caller receives not-found rather than a role denial that discloses existence. Filtering parameters referencing resources outside the caller's workspace SHALL NOT widen visibility beyond the caller's workspace. The boundary SHALL NOT change successful response shapes, pagination semantics, or the comparison algorithm, and all four endpoints SHALL remain read-only.

#### Scenario: System scope reads across workspaces
- **WHEN** an explicit system-scope API key calls any of the four run read endpoints for runs in any workspace
- **THEN** the requests are processed under the endpoints' existing semantics without workspace restriction from this boundary

#### Scenario: Missing workspace non-system identity is denied
- **WHEN** a validly authenticated non-system identity without an active workspace calls any of the four run read endpoints
- **THEN** the request is denied (not-found or authorization denied) and the response SHALL NOT fall back to an unscoped global query

#### Scenario: Viewer role suffices for run reads
- **WHEN** a viewer-role member of workspace W1 calls the run listing, detail, scores, or comparison endpoints for W1 runs
- **THEN** all four requests succeed with the same responses a higher role would receive

#### Scenario: Boundary keeps read endpoints read-only
- **WHEN** the access boundary rejects a request for any of the four run read endpoints
- **THEN** no run, score, or comparison state is created, modified, or deleted by the rejected request

#### Scenario: Both comparison ownership checks precede role denial
- **WHEN** an authenticated identity without a workspace role compares a run in its active workspace with an invisible run, regardless of which run is the baseline
- **THEN** the response SHALL be the same 404 as a missing run; the endpoint SHALL check both inputs' ownership before returning a role-based 403

### Requirement: Evaluation run execution access boundary

`POST /api/evaluation/runs` SHALL resolve the target dataset's ownership before enforcing a minimum editor role: for non-system identities, a dataset that does not exist, is soft-deleted, or belongs to another workspace SHALL produce one constant not-found (404) response that does not echo identifiers, and ownership SHALL be checked before the role floor so a role denial never discloses dataset existence. A non-system identity without an active workspace SHALL receive that same constant dataset not-found without querying the dataset. Explicit system-scope API keys SHALL execute by dataset ID across workspaces, with the target workspace taken from the resolved dataset rather than the caller's active workspace. The role floor SHALL be editor: viewer and role-less membership in the dataset's workspace SHALL be denied (403), and the denial SHALL precede evaluator-name validation. Every denial SHALL occur before any run or score row is created and before any evaluator, model, or pipeline executes. A successful execution SHALL persist the run — and the scores the engine derives from it — with the dataset's real workspace, SHALL keep the existing 201 response shape, tag filtering, empty-dataset runs, unknown-evaluator 422, and `manual`/`pipeline`/`auto` answer-source semantics, and the persisted run SHALL remain readable through the evaluation run read boundary. Items in the dataset SHALL participate based on the parent dataset's workspace, regardless of the item rows' own historical workspace values.

#### Scenario: Invisible dataset yields uniform not-found before role denial
- **WHEN** an authenticated non-system identity submits a create-run request whose dataset does not exist, is soft-deleted, or belongs to another workspace, regardless of the caller's workspace role
- **THEN** the response SHALL be 404 with one consistent body across all conditions, without echoing the dataset ID, and no run or score SHALL be created

#### Scenario: Missing workspace identity receives constant dataset not-found
- **WHEN** a validly authenticated non-system identity without an active workspace submits a create-run request
- **THEN** the response SHALL be the same 404 as a missing dataset, the dataset SHALL NOT be queried, and no run or score SHALL be created

#### Scenario: Editor role required in the dataset's workspace
- **WHEN** a viewer or role-less member of the dataset's workspace submits a create-run request, even with an unknown evaluator name
- **THEN** the response SHALL be 403 authorization denied (preceding evaluator validation) and no run, score, evaluator, model, or pipeline execution SHALL occur

#### Scenario: System scope executes across workspaces
- **WHEN** an explicit system-scope API key, with or without an active workspace different from the dataset's, submits a create-run request by dataset ID
- **THEN** the execution SHALL proceed and the created run and its scores SHALL land in the resolved dataset's workspace

#### Scenario: Runs and scores persist to the dataset's workspace and are readable back
- **WHEN** an editor of the dataset's workspace successfully creates a run
- **THEN** the persisted run and every score row SHALL carry the dataset's real workspace (not the zero-UUID default), a same-workspace viewer SHALL read the run back through the listing, detail, and scores endpoints, and members of other workspaces SHALL NOT observe it

#### Scenario: Answer-generation errors remain readable as persisted scores
- **WHEN** an authorized create-run request uses `auto` or `pipeline` and answer generation fails for an item
- **THEN** the response SHALL remain 201, each configured evaluator SHALL have a persisted error score with value `-1.0`, the existing failure reasoning, and the run's workspace; the failed item SHALL NOT invoke evaluators, sibling items SHALL continue, error scores SHALL be excluded from metric averages, and a same-workspace viewer SHALL read the same scores through the run detail and scores endpoints
