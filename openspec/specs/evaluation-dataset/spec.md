# Capability: evaluation-dataset

> Synced from archive of change `ai-synthesized-evaluation-dataset`. The
> previous spec described fields (`version`, `baseline_run_id`, `is_locked`,
> `default_threshold`, `assertions`) that were never implemented. The
> MODIFIED delta replaces the spec with what is actually shipped in this
> change and explicitly defers the unimplemented fields to a follow-up.
> Later sync: `known-bad-exemption` (7.3c) added the known-bad exemption
> marker fields, the marking requirement, and exemption-status filtering.

## MODIFIED Requirements

### Requirement: Dataset item management
The system SHALL provide methods to add, list, update, and remove items within a dataset. Each item SHALL contain: `query: str`, `expected_answer: str | None`, `context: list[str] | None`, `metadata: dict | None`, `tags: list[str] | None`, `known_bad: bool` (default `False`), `known_bad_reason: str | None`, `known_bad_marked_by: UUID | None`, `known_bad_marked_at: datetime | None`, and `known_bad_expires_at: datetime | None`. Items SHALL be persisted with the `tags` field stored as a JSON column on `EvaluationItemModel`. The `tags` field and the known-bad marker fields SHALL be retrievable in `EvaluationItemReadSchema`, and both SHALL round-trip through JSON import/export.

#### Scenario: Add items with tags
- **WHEN** a user adds a batch of items where each item has `tags=["smoke", "regression"]`
- **THEN** the system SHALL validate each item has a non-empty `query` field, persist all items including their tags as JSON, and return the count of added items; items SHALL be persisted with `known_bad` defaulting to `False` and all known-bad marker fields defaulting to `None`

#### Scenario: List items with pagination
- **WHEN** a user lists items in a dataset with page and page_size parameters
- **THEN** the system SHALL return items ordered by creation time with total count, including the `tags` field and the known-bad marker fields per item

#### Scenario: Tags round-trip through export and import
- **WHEN** a dataset with tagged items is exported to JSON and then re-imported
- **THEN** the imported items SHALL retain the same `tags` values, and every item's known-bad marker fields (`known_bad_reason`, `known_bad_marked_by`, `known_bad_marked_at`, `known_bad_expires_at`) SHALL be preserved exactly

### Requirement: Tag-filtered dataset queries
The system SHALL support filtering items by tags when listing items within a dataset. The filter SHALL match items whose `tags` JSON array contains ANY of the specified tags (OR semantics). The filter SHALL be exposed via the existing `EvaluationItemService.list_items(dataset_id, tags: list[str] | None, page, page_size)` method.

#### Scenario: List items filtered by single tag
- **WHEN** a user lists items with `tags=["intent:prompt_injection_basic"]`
- **THEN** the system SHALL return only items whose `tags` contain that value

#### Scenario: List items filtered by multiple tags
- **WHEN** a user lists items with `tags=["synthetic", "strategy:adversarial"]`
- **THEN** the system SHALL return items whose `tags` contain "synthetic" OR "strategy:adversarial"

#### Scenario: List with no tag filter returns all
- **WHEN** `tags` parameter is omitted or empty
- **THEN** the system SHALL return all items regardless of tags

### Requirement: Tag-filtered evaluation runs
The system SHALL support filtering items by tags when running evaluations. The `EvaluationRunCreateSchema` SHALL accept `tags: list[str] | None`. When `tags` is provided, the engine SHALL run evaluators only against items matching ANY of the specified tags. When omitted, the engine SHALL run against all items in the dataset (current behavior preserved).

#### Scenario: Run evaluation only on synthetic adversarial items
- **WHEN** an evaluation run request specifies `tags=["strategy:adversarial"]`
- **THEN** the engine SHALL score only items whose tags include that value; results SHALL report the filtered item count

#### Scenario: Run evaluation on all items by default
- **WHEN** the run request omits `tags`
- **THEN** the engine SHALL score all items in the dataset (backward compatible)

### Requirement: Dataset synthesis writes items with provenance tags
The synthesis service SHALL set the `tags` JSON column on every synthesized `EvaluationItemModel` to include `"synthetic"` and the strategy name. For adversarial synthesis, tags SHALL additionally include `"intent:<adversarial_intent>"`. For generation/evolution synthesis with a seed dataset, tags SHALL additionally include `"seed_dataset:<uuid>"`. See `dataset-synthesis` capability for full details.

#### Scenario: Tags on synthesis output
- **WHEN** a synthesis job completes successfully
- **THEN** every persisted item SHALL carry the strategy and intent tags as defined in the `dataset-synthesis` capability

### Requirement: Dataset import/export preserves tags
The JSON import/export format SHALL include the `tags` array for each item alongside `query`, `expected_answer`, `context`, and `metadata`.

#### Scenario: Export to JSON with tags
- **WHEN** a user exports a dataset
- **THEN** the system SHALL produce a JSON file containing all items with their `query`, `expected_answer`, `context`, `metadata`, and `tags` fields

#### Scenario: Import from JSON with tags
- **WHEN** a user imports a JSON file containing items with `tags` fields
- **THEN** the system SHALL persist tags alongside the other item fields

### Requirement: Dataset CRUD operations (carried forward, drift-deferred)
The system SHALL provide an `EvaluationDatasetService` with async methods: `create_dataset()`, `get_dataset()`, `list_datasets()`, `update_dataset()`, `delete_dataset()`. Datasets SHALL continue to support the existing fields (`name`, `description`, `metadata_`, `workspace_id`) at the ORM level. The legacy spec fields `version`, `baseline_run_id`, `is_locked`, and `default_threshold` described in the previous spec revision are NOT implemented in this change — they are documented drift to be reconciled by a follow-up change. This change does not commit to those legacy fields' behavior.

#### Scenario: Create evaluation dataset
- **WHEN** a user creates a dataset with a name and optional description
- **THEN** the system SHALL create an `EvaluationDatasetModel` record and return the dataset with generated UUID and timestamps

#### Scenario: Delete dataset with items
- **WHEN** a user deletes a dataset that contains evaluation items
- **THEN** the system SHALL cascade-delete all associated items and return success

## ADDED Requirements

### Requirement: Known-bad exemption marking
The system SHALL allow marking a dataset item as known-bad (exempt) and clearing the mark through the dataset item update API. Setting `known_bad=true` SHALL require a non-empty `known_bad_reason`. When the mark is set, the service SHALL fill `known_bad_marked_by` with the current authenticated user's id and `known_bad_marked_at` with the current server timestamp; clients SHALL NOT be able to set the provenance fields directly. Clearing the mark (`known_bad=false`) SHALL reset `known_bad_reason`, `known_bad_marked_by`, `known_bad_marked_at`, and `known_bad_expires_at` to `None`. Listing items SHALL support filtering by exemption status. `known_bad_expires_at` is a reserved field: v1 SHALL persist it but SHALL NOT enforce any expiry behavior.

#### Scenario: Mark an item known-bad with reason
- **WHEN** an item is updated with `known_bad=true` and a non-empty `known_bad_reason`
- **THEN** the item is persisted with `known_bad=true`, the supplied reason, and server-filled `known_bad_marked_by` / `known_bad_marked_at`

#### Scenario: Mark without reason is rejected
- **WHEN** an item is updated with `known_bad=true` and no `known_bad_reason` (or an empty one)
- **THEN** the request SHALL be rejected with a validation error and the item SHALL remain unchanged

#### Scenario: Clear the mark resets provenance
- **WHEN** a known-bad item is updated with `known_bad=false`
- **THEN** `known_bad_reason`, `known_bad_marked_by`, `known_bad_marked_at`, and `known_bad_expires_at` are all reset to `None`

#### Scenario: Filter items by exemption status
- **WHEN** items are listed with an exemption-status filter (only known-bad / only active)
- **THEN** only matching items are returned; without the filter all items are returned

#### Scenario: Import/export round-trips exemption
- **WHEN** a dataset containing known-bad items is exported and re-imported
- **THEN** the re-imported items carry identical exemption markers without requiring re-marking
