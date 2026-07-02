## ADDED Requirements

### Requirement: Dataset management API
The system SHALL expose REST endpoints at `/api/evaluation/datasets` for dataset CRUD and `/api/evaluation/datasets/{dataset_id}/items` for item management.

#### Scenario: Create dataset via API
- **WHEN** a POST request is sent to `/api/evaluation/datasets` with `{"name": "test-set", "description": "..."}`
- **THEN** the API SHALL return 201 with the created dataset including generated `id`, `created_at`, `updated_at`

#### Scenario: Add items to dataset
- **WHEN** a POST request is sent to `/api/evaluation/datasets/{id}/items` with a list of items
- **THEN** the API SHALL validate each item, persist valid items, and return 201 with count of added items

#### Scenario: List datasets with pagination
- **WHEN** a GET request is sent to `/api/evaluation/datasets?page=1&page_size=20`
- **THEN** the API SHALL return a paginated list of datasets with total count

#### Scenario: Delete dataset
- **WHEN** a DELETE request is sent to `/api/evaluation/datasets/{id}`
- **THEN** the API SHALL cascade-delete the dataset and all its items, returning 204

### Requirement: Evaluation run API
The system SHALL expose REST endpoints at `/api/evaluation/runs` for creating and retrieving evaluation runs.

#### Scenario: Create evaluation run
- **WHEN** a POST request is sent to `/api/evaluation/runs` with `{"dataset_id": "...", "evaluators": ["faithfulness", "context_precision"]}`
- **THEN** the API SHALL execute the specified evaluators against the dataset items, persist the run with all scores, and return 201 with the `EvaluationRunResult`

#### Scenario: List evaluation runs
- **WHEN** a GET request is sent to `/api/evaluation/runs?dataset_id=...`
- **THEN** the API SHALL return runs filtered by dataset_id with summary statistics

#### Scenario: Get run scores
- **WHEN** a GET request is sent to `/api/evaluation/runs/{id}/scores`
- **THEN** the API SHALL return all individual scores for the run, grouped by evaluator metric

### Requirement: Authentication required
All evaluation API endpoints SHALL require authentication via the existing JWT/API key middleware.

#### Scenario: Unauthenticated request
- **WHEN** a request is sent without valid authentication credentials
- **THEN** the API SHALL return 401 Unauthorized
