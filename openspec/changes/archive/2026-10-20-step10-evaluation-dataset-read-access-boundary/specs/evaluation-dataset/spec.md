# Spec Delta — evaluation-dataset

## ADDED Requirements

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
