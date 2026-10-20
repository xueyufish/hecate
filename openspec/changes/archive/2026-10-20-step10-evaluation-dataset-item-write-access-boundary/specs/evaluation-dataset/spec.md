# Spec Delta — evaluation-dataset

## ADDED Requirements

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
