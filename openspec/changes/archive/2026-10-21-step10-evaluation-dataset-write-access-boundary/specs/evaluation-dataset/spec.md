# Spec Delta — evaluation-dataset

## ADDED Requirements

### Requirement: Evaluation dataset write access boundary

The dataset create, update, and delete endpoints (`POST /api/evaluation/datasets`, `PUT /api/evaluation/datasets/{dataset_id}`, `DELETE /api/evaluation/datasets/{dataset_id}`) SHALL enforce one shared write access boundary. Update and delete SHALL resolve the parent dataset first: a dataset that does not exist, is soft-deleted, or belongs to another workspace SHALL produce the same not-found (404) response, and the response SHALL NOT echo the dataset identifier. An authenticated non-system identity SHALL additionally hold a minimum editor role in the dataset's workspace, with ownership checked before role so that a cross-workspace caller receives not-found rather than a role denial that discloses existence. Explicit system-scope identities SHALL update and delete across workspaces. Non-system creation SHALL require at least an editor role in the active workspace. Creation SHALL check workspace presence before role authorization and require an explicit active workspace for every identity, including system scope: a create request without an active workspace SHALL be denied with 403 and a distinct `NO_WORKSPACE` code, the REST API SHALL NOT offer a workspace-selection parameter in this boundary, and a created dataset SHALL belong to the creating identity's active workspace — never to the historical zero-UUID owner. A legitimate editor delete SHALL cascade-soft-delete all items of the dataset regardless of the items' own workspace values, including historical zero-UUID rows. The boundary SHALL NOT change successful response shapes, update semantics, or the cascade behavior, and rejected requests SHALL have zero side effects.

#### Scenario: Writes on an invisible dataset are not possible
- **WHEN** update or delete is called for a dataset that does not exist, is soft-deleted, or belongs to another workspace
- **THEN** the response SHALL be 404 with the same body for all conditions ("Dataset not found"), without echoing the dataset identifier, and the dataset SHALL remain unchanged

#### Scenario: Editor role floor applies after ownership
- **WHEN** an authenticated identity without a workspace role calls update or delete for a dataset that belongs to another workspace
- **THEN** the response SHALL be the same 404 as a missing dataset, not a role-based 403; an editor-role member of the dataset's workspace receives the same successful response an admin would receive

#### Scenario: Viewer and role-less members cannot write
- **WHEN** an identity whose active workspace contains the dataset but whose workspace membership role is below editor (including viewer and role-less members) calls update or delete
- **THEN** the response SHALL be 403 authorization denied, and the dataset's content and deletion state SHALL remain unchanged

#### Scenario: System scope updates and deletes across workspaces
- **WHEN** an explicit system-scope identity updates or deletes an existing dataset in any workspace
- **THEN** the request is processed under the endpoint's existing semantics without workspace restriction from this boundary

#### Scenario: Create requires an explicit active workspace
- **WHEN** any identity — including system scope — calls create without an active workspace
- **THEN** the response SHALL be 403 with the distinct `NO_WORKSPACE` code; the REST boundary SHALL NOT fall back to the historical zero-UUID owner and SHALL NOT accept a workspace-selection parameter

#### Scenario: Created datasets belong to the creating identity's active workspace
- **WHEN** a member of workspace W creates a dataset
- **THEN** the persisted dataset row SHALL carry `workspace_id` W and never the historical zero-UUID owner

#### Scenario: Legitimate delete cascades all items regardless of item workspace
- **WHEN** an editor (or higher) of a visible dataset deletes it, and the dataset contains both current-workspace items and historical zero-UUID items
- **THEN** the dataset and ALL its items SHALL be soft-deleted together — the cascade SHALL NOT filter by the items' own workspace values — and the response SHALL keep the existing 204 shape

#### Scenario: Boundary preserves update semantics and rejected-request side-effect freedom
- **WHEN** dataset write requests succeed or are rejected by the boundary
- **THEN** successful updates keep their existing request/response semantics; rejected requests leave dataset rows, item rows (including known-bad provenance and deletion markers), and all other state unchanged, verified against full-field database snapshots

#### Scenario: Creation requires editor authorization after workspace presence
- **WHEN** an authenticated non-system identity with an active workspace but a viewer or missing role calls create
- **THEN** the response SHALL be 403 FORBIDDEN and no dataset SHALL be created; missing active workspace SHALL instead produce NO_WORKSPACE before the role check
