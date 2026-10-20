# Capability: evaluation-dataset

> Synced from archive of change `ai-synthesized-evaluation-dataset`. The
> previous spec described fields (`version`, `baseline_run_id`, `is_locked`,
> `default_threshold`, `assertions`) that were never implemented. The
> MODIFIED delta replaces the spec with what is actually shipped in this
> change and explicitly defers the unimplemented fields to a follow-up.
> Later sync: `known-bad-exemption` (7.3c) added the known-bad exemption
> marker fields, the marking requirement, and exemption-status filtering.

## Purpose

Defines evaluation datasets: item management, tag-filtered queries and runs, synthesis provenance tags, tag-preserving import/export, CRUD, and known-bad exemption marking.

## Requirements

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

### Requirement: Evaluation dataset read access boundary

The dataset listing, detail, and item-listing endpoints (`GET /api/evaluation/datasets`, `GET /api/evaluation/datasets/{dataset_id}`, `GET /api/evaluation/datasets/{dataset_id}/items`) SHALL enforce one shared read access boundary: an authenticated non-system identity SHALL only observe evaluation datasets belonging to its active workspace, with a minimum viewer role in that workspace; the listing's items and `total` SHALL count only datasets within the caller's workspace. A dataset that does not exist, is soft-deleted, or belongs to another workspace SHALL produce the same not-found (404) response for a given endpoint, and the response SHALL NOT disclose which condition applied or echo the dataset identifier. Ownership SHALL be checked before role so that a cross-workspace caller receives not-found rather than a role denial that discloses existence. Explicit system-scope identities SHALL read across workspaces; a non-system identity without an active workspace SHALL be denied and SHALL NOT degrade to a global query. The boundary SHALL NOT change successful response shapes, item filter semantics (`tags`, `known_bad`), or pagination behavior, and the three endpoints SHALL remain read-only.

#### Scenario: Listing is scoped to the caller workspace
- **WHEN** datasets exist in workspaces W1 and W2 and a W1 member calls `GET /api/evaluation/datasets`
- **THEN** only W1 datasets are returned and `total` counts only W1 datasets across pages

#### Scenario: System scope reads across workspaces
- **WHEN** an explicit system-scope identity calls any of the three dataset read endpoints for datasets in any workspace
- **THEN** the requests are processed under the endpoints' existing semantics without workspace restriction from this boundary

#### Scenario: Missing workspace non-system identity is denied
- **WHEN** a validly authenticated non-system identity without an active workspace calls any of the three dataset read endpoints
- **THEN** the request is denied and the response SHALL NOT fall back to an unscoped global query

#### Scenario: Detail for invisible dataset is indistinguishable from missing
- **WHEN** a W1 member requests `/api/evaluation/datasets/{id}` for a dataset that is soft-deleted or belongs to W2, and for a dataset ID that does not exist at all
- **THEN** all three requests SHALL receive the same 404 response body without echoing the dataset identifier

#### Scenario: Items of an invisible dataset return not-found
- **WHEN** a W1 member calls `GET /api/evaluation/datasets/{id}/items` for a dataset that does not exist, is soft-deleted, or belongs to W2
- **THEN** the response SHALL be 404 with the same body for all three conditions; the API SHALL NOT return an item list or an empty-list success that would reveal whether the dataset exists elsewhere

#### Scenario: Viewer role suffices and ownership precedes role
- **WHEN** an authenticated identity without a workspace role requests a dataset that belongs to another workspace
- **THEN** the response SHALL be the same 404 as a missing dataset, not a role-based 403; a viewer-role member of the dataset's workspace receives the same successful response a higher role would receive

#### Scenario: Role floor inside the caller workspace
- **WHEN** an identity whose active workspace contains the dataset but whose workspace membership role is below viewer (or absent) calls any of the three dataset read endpoints
- **THEN** the response SHALL be 403 authorization denied and no dataset content SHALL be returned

#### Scenario: Boundary keeps read endpoints read-only
- **WHEN** the access boundary rejects a request for any of the three dataset read endpoints
- **THEN** no dataset, item, or related evaluation state is created, modified, or deleted by the rejected request

#### Scenario: Visible-dataset filters and pagination keep existing semantics
- **WHEN** a viewer (or higher) of the dataset's workspace lists items with `tags`, `known_bad`, `page`, or `page_size` parameters, or lists datasets with pagination
- **THEN** the responses keep the existing filter (OR tag semantics, exemption-status filter), ordering, and pagination behavior; a visible dataset with no matching items returns 200 with an empty list. For item listing, `known_bad` filtering applies before counting and pagination, while tag filtering applies to the selected page and does not reduce `total`; a dataset containing no items returns `total` 0

### Requirement: Evaluation dataset item write access boundary

The item add, update (known-bad marking), and delete endpoints (`POST /api/evaluation/datasets/{dataset_id}/items`, `PATCH /api/evaluation/datasets/{dataset_id}/items/{item_id}`, `DELETE /api/evaluation/datasets/{dataset_id}/items/{item_id}`) SHALL enforce one shared write access boundary. The parent dataset SHALL be resolved first: a dataset that does not exist, is soft-deleted, or belongs to another workspace SHALL produce the same not-found (404) response, and the response SHALL NOT echo the dataset identifier. An item SHALL be writable only when it exists, is not soft-deleted, and belongs to the dataset in the request path; an item failing any of these conditions SHALL produce a 404 whose body is identical for all conditions and SHALL NOT echo any resource identifier. Authorization SHALL be anchored to the parent dataset's workspace and the item-to-dataset binding, never to the item row's historical `workspace_id` field. An authenticated non-system identity SHALL additionally hold a minimum editor role in the dataset's workspace, with ownership checked before role so that a cross-workspace caller receives not-found rather than a role denial that discloses existence. Explicit system-scope identities SHALL write across workspaces; a non-system identity without an active workspace SHALL be denied and SHALL NOT degrade to an unscoped write. Newly added items SHALL inherit the parent dataset's workspace; historical item rows SHALL NOT be backfilled. The boundary SHALL NOT change successful response shapes, known-bad marking semantics, server-side provenance filling, blank-query skipping, or blank-query skipping's effect on the added count, and the endpoints' existing roles in run execution SHALL remain unchanged.

#### Scenario: Items of an invisible dataset are not writable
- **WHEN** any of the three item write endpoints is called for a dataset that does not exist, is soft-deleted, or belongs to another workspace
- **THEN** the response SHALL be 404 with the same body for all three conditions, without echoing the dataset identifier; no item SHALL be added, marked, or deleted

#### Scenario: Cross-dataset and cross-workspace item references are not writable
- **WHEN** a caller passes an `item_id` that belongs to a different dataset or another workspace while the path dataset is visible to them
- **THEN** PATCH and DELETE SHALL return 404 with the same body as a missing item, and no record outside the path dataset SHALL be modified or deleted

#### Scenario: Detail of an unbindable item echoes no identifier
- **WHEN** an item fails the existence, soft-delete, or binding condition
- **THEN** all conditions SHALL receive the same 404 response body, and the body SHALL NOT contain any item, dataset, or caller identifier

#### Scenario: Editor role floor applies after ownership
- **WHEN** an authenticated identity without a workspace role calls an item write endpoint for a dataset that belongs to another workspace
- **THEN** the response SHALL be the same 404 as a missing dataset, not a role-based 403; an editor-role member of the dataset's workspace receives the same successful response an admin would receive

#### Scenario: Viewer role is insufficient for item writes
- **WHEN** an identity whose active workspace contains the dataset but whose workspace membership role is below editor (including viewer and role-less members) calls any of the three item write endpoints
- **THEN** the response SHALL be 403 authorization denied, no item SHALL be added, marked, or deleted, and known-bad provenance SHALL remain unchanged

#### Scenario: System scope writes across workspaces
- **WHEN** an explicit system-scope identity calls any of the three item write endpoints for datasets in any workspace
- **THEN** the requests are processed under the endpoints' existing semantics without workspace restriction from this boundary

#### Scenario: Missing workspace non-system identity is denied
- **WHEN** a validly authenticated non-system identity without an active workspace calls any of the three item write endpoints
- **THEN** the request is denied and the operation SHALL NOT fall back to an unscoped write

#### Scenario: New items inherit the parent dataset workspace
- **WHEN** items are added to a dataset whose workspace is W
- **THEN** every persisted new item row SHALL carry `workspace_id` W; historical item rows SHALL NOT be backfilled, and existing items with the historical zero-UUID workspace remain fully readable and writable under the parent dataset's boundary

#### Scenario: Boundary preserves write semantics and success shapes
- **WHEN** an editor (or higher) adds, marks, or deletes items in a visible dataset
- **THEN** the responses keep the existing shapes (`{"added": n}` with 201, the updated item representation, 204), blank-query entries are skipped and excluded from the added count, known-bad marking keeps its server-side provenance filling and clearing reset, and rejections of any request leave item content, deletion markers, and known-bad provenance unchanged
