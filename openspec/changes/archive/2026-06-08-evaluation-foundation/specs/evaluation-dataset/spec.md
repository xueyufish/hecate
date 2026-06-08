## ADDED Requirements

### Requirement: Dataset CRUD operations
The system SHALL provide an `EvaluationDatasetService` in `services/evaluation/dataset_service.py` with async methods: `create_dataset()`, `get_dataset()`, `list_datasets()`, `update_dataset()`, `delete_dataset()`.

#### Scenario: Create evaluation dataset
- **WHEN** a user creates a dataset with a name and optional description
- **THEN** the system SHALL create an `EvaluationDatasetModel` record in PostgreSQL and return the dataset with generated UUID and timestamps

#### Scenario: Delete dataset with items
- **WHEN** a user deletes a dataset that contains evaluation items
- **THEN** the system SHALL cascade-delete all associated items and return success

### Requirement: Dataset item management
The system SHALL provide methods to add, list, update, and remove items within a dataset. Each item SHALL contain: `query: str`, `expected_answer: str | None`, `context: list[str] | None`, `metadata: dict | None`.

#### Scenario: Add items to dataset
- **WHEN** a user adds a batch of items to a dataset
- **THEN** the system SHALL validate each item has a non-empty `query` field, persist all items, and return the count of added items

#### Scenario: List items with pagination
- **WHEN** a user lists items in a dataset with page and page_size parameters
- **THEN** the system SHALL return items ordered by creation time with total count

### Requirement: Dataset import/export
The system SHALL support importing datasets from JSON files and exporting datasets to JSON format.

#### Scenario: Import from JSON
- **WHEN** a user imports a JSON file containing an array of `{query, expected_answer, context}` objects
- **THEN** the system SHALL validate the format, create items, and return import statistics (total, valid, skipped)

#### Scenario: Export to JSON
- **WHEN** a user exports a dataset
- **THEN** the system SHALL produce a JSON file containing all items with their query, expected_answer, context, and metadata fields
