# Delta: dataset-versioning

## ADDED Requirements

### Requirement: Named dataset version objects

The system SHALL support named, immutable version objects for evaluation datasets. `POST /api/evaluation/datasets/{dataset_id}/versions` with a `name` (and optional `description`) SHALL freeze the dataset's current live items into a version record containing the frozen item list, a `content_hash` computed with the same canonical serialization and content-field projection used by run dataset snapshots, and the creating user as `created_by`. Version names SHALL be unique within their dataset, and soft-deleted versions SHALL keep their names reserved. A version's frozen items, name, and content hash SHALL NOT be modifiable after creation; the system SHALL NOT expose any update path for them. `GET /api/evaluation/datasets/{dataset_id}/versions` SHALL list versions and `GET .../versions/{version_id}` SHALL return one version including its frozen items. `DELETE .../versions/{version_id}` SHALL soft-delete the version; existing runs that referenced it SHALL remain unaffected because runs carry their own embedded snapshot.

#### Scenario: Create freezes the current live items

- **WHEN** a dataset has 10 live items and a version is created with `name: "v3.0"`, then an item is added to the live dataset
- **THEN** the version SHALL contain exactly the 10 items frozen at creation time, its `content_hash` SHALL equal the hash those items would produce in a run dataset snapshot, and the live dataset SHALL remain unchanged (12 items)

#### Scenario: Duplicate name rejected

- **WHEN** a version named `v3.0` already exists for the dataset (even if soft-deleted) and another version with the same name is created
- **THEN** the API SHALL respond with 409 and the existing version SHALL remain unchanged

#### Scenario: Version is immutable

- **WHEN** any request attempts to modify a version's frozen items, name, or content hash after creation
- **THEN** the system SHALL NOT expose such an update path, and the version's `content_hash` SHALL remain stable for its lifetime

#### Scenario: Soft delete does not affect runs

- **WHEN** a version referenced by past runs is soft-deleted
- **THEN** the version SHALL disappear from list results, the name SHALL remain reserved, and the past runs' summaries, snapshots, and version references SHALL remain readable and unchanged

### Requirement: Version checkout restores the live dataset

`POST /api/evaluation/datasets/{dataset_id}/versions/{version_id}/checkout` SHALL replace the dataset's live items with the version's frozen items: the current live items SHALL be soft-deleted, and the version's items SHALL be inserted as new item records preserving their content fields, tags, metadata, and known-bad markers together with their provenance (`known_bad_reason`, `known_bad_marked_by`, `known_bad_marked_at`). The response SHALL include a diff summary describing what changed (added / removed / changed counts versus the previous live state). Checkout against a deleted or non-existent version SHALL return 404.

#### Scenario: Checkout replaces live items

- **WHEN** a checkout is performed on a version frozen with 10 items while the live dataset currently holds 12
- **THEN** the live dataset SHALL contain exactly the version's 10 items under new item ids, the previous 12 live items SHALL be soft-deleted, and the response SHALL describe the transition

#### Scenario: Checkout preserves known-bad markers

- **WHEN** a version item was marked known-bad (with reason and provenance) before freezing and the version is checked out
- **THEN** the restored live item SHALL be known-bad with the same reason and provenance values

#### Scenario: Checkout is destructive but auditable

- **WHEN** a checkout replaces live items that were edited after the version was frozen
- **THEN** the checkout SHALL proceed without an API-level confirmation parameter, and the response SHALL include the diff summary needed to audit what was replaced

### Requirement: Version diff endpoint

`GET /api/evaluation/datasets/{dataset_id}/versions/{version_id}/diff?against={version_id|"live"}` SHALL align items by item id and classify them into `added`, `removed`, and `changed` using the content-only field projection (query, expected_answer, context, tags, metadata, known_bad). Changed items SHALL include field-level deltas. When `against=live`, the comparison SHALL run against the dataset's current live items; when `against` is another version id, against that version's frozen items. The response shape SHALL be consistent with the run-compare `dataset_drift` style.

#### Scenario: Version versus version

- **WHEN** version B added one item and changed another item's `expected_answer` relative to version A
- **THEN** the diff SHALL report `added` with the new item, `changed` with the `expected_answer` delta, and no `removed` entries

#### Scenario: Version versus live

- **WHEN** `against=live` and the live dataset has drifted from the version (items edited and one deleted)
- **THEN** the diff SHALL classify the edited items as `changed` with field deltas and the deleted item as `removed`

#### Scenario: Identical content produces an empty diff

- **WHEN** the two compared sides contain the same items with the same content
- **THEN** the diff SHALL report empty `added`, `removed`, and `changed` lists
