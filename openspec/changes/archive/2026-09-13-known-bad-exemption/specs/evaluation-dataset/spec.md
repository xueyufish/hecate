# Delta: evaluation-dataset

## MODIFIED Requirements

### Requirement: Dataset item management

The system SHALL provide methods to add, list, update, and remove items within a dataset. Each item SHALL contain: `query: str`, `expected_answer: str | None`, `context: list[str] | None`, `metadata: dict | None`, `tags: list[str] | None`, `known_bad: bool` (default `False`), `known_bad_reason: str | None`, `known_bad_marked_by: UUID | None`, `known_bad_marked_at: datetime | None`, and `known_bad_expires_at: datetime | None`. Items SHALL be persisted with the `tags` field stored as a JSON column on `EvaluationItemModel`. The `tags` field and the known-bad marker fields SHALL be retrievable in `EvaluationItemReadSchema`, and both SHALL round-trip through JSON import/export.

#### Scenario: Add items with tags

- **WHEN** items are added with `tags`
- **THEN** the items are persisted with `known_bad` defaulting to `False` and all known-bad marker fields defaulting to `None`

#### Scenario: List items with pagination

- **WHEN** items are listed with pagination parameters
- **THEN** each returned item includes `known_bad` and the known-bad marker fields alongside the existing fields

#### Scenario: Tags round-trip through export and import

- **WHEN** a dataset is exported to JSON and imported into a new dataset
- **THEN** every item's `known_bad` marker fields are preserved exactly, including `known_bad_reason`, `known_bad_marked_by`, `known_bad_marked_at`, and `known_bad_expires_at`

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
