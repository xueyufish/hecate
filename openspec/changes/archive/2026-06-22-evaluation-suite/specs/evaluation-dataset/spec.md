## MODIFIED Requirements

### Requirement: Dataset CRUD operations
The system SHALL provide an `EvaluationDatasetService` in `services/evaluation/dataset_service.py` with async methods: `create_dataset()`, `get_dataset()`, `list_datasets()`, `update_dataset()`, `delete_dataset()`. Datasets SHALL support `version` (String, default "v1.0"), `baseline_run_id` (UUID FK, nullable), `is_locked` (Boolean, default False), and `default_threshold` (Float, nullable) fields.

#### Scenario: Create evaluation dataset
- **WHEN** a user creates a dataset with a name and optional description
- **THEN** the system SHALL create an `EvaluationDatasetModel` record with version="v1.0", is_locked=False, and return the dataset with generated UUID and timestamps

#### Scenario: Delete dataset with items
- **WHEN** a user deletes a dataset that contains evaluation items
- **THEN** the system SHALL cascade-delete all associated items and return success

#### Scenario: Lock dataset prevents modifications
- **WHEN** a user attempts to add items to a dataset with `is_locked=True`
- **THEN** the system SHALL reject the operation with 409 Conflict

#### Scenario: Set baseline run on dataset
- **WHEN** a user updates a dataset's `baseline_run_id` to a completed run's ID
- **THEN** subsequent regression comparisons SHALL use this run as the baseline

### Requirement: Dataset item management
The system SHALL provide methods to add, list, update, and remove items within a dataset. Each item SHALL contain: `query: str`, `expected_answer: str | None`, `context: list[str] | None`, `metadata: dict | None`, `assertions: list[dict] | None`, `tags: list[str] | None`.

#### Scenario: Add items to dataset
- **WHEN** a user adds a batch of items to a dataset
- **THEN** the system SHALL validate each item has a non-empty `query` field, persist all items, and return the count of added items

#### Scenario: List items with pagination
- **WHEN** a user lists items in a dataset with page and page_size parameters
- **THEN** the system SHALL return items ordered by creation time with total count

#### Scenario: Add item with assertions
- **WHEN** a user creates an item with `assertions=[{"type": "faithfulness", "threshold": 0.85}]`
- **THEN** the item SHALL be persisted with the assertions JSON and used during pass/fail evaluation

#### Scenario: Add item with tags
- **WHEN** a user creates an item with `tags=["smoke", "regression"]`
- **THEN** the item SHALL be included in tag-filtered evaluation runs matching either tag

### Requirement: Dataset import/export
The system SHALL support importing datasets from JSON files and exporting datasets to JSON format. The JSON format SHALL include assertions and tags fields for each item.

#### Scenario: Import from JSON with assertions
- **WHEN** a user imports a JSON file containing items with `assertions` and `tags` fields
- **THEN** the system SHALL persist assertions and tags alongside query, expected_answer, and context

#### Scenario: Export to JSON with assertions
- **WHEN** a user exports a dataset
- **THEN** the system SHALL produce a JSON file containing all items with their query, expected_answer, context, metadata, assertions, and tags fields
