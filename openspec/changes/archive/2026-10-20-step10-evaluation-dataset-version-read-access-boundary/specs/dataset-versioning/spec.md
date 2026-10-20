# Spec Delta — dataset-versioning

## ADDED Requirements

### Requirement: Dataset version read access boundary

The version listing, detail, and diff endpoints (`GET /api/evaluation/datasets/{dataset_id}/versions`, `GET .../versions/{version_id}`, `GET .../versions/{version_id}/diff`) SHALL enforce one shared read access boundary. The parent dataset SHALL be resolved first: a dataset that does not exist, is soft-deleted, or belongs to another workspace SHALL produce the same not-found (404) response on all three endpoints, and the response SHALL NOT echo the dataset identifier. A version SHALL then be read only when it exists, is not soft-deleted, and belongs to the dataset in the request path; a version failing any of these conditions — including a diff target version — SHALL produce a 404 whose body is identical for all conditions and SHALL NOT echo any resource identifier; an invisible diff target SHALL NOT surface as a server error. An authenticated non-system identity SHALL additionally hold a minimum viewer role in the dataset's workspace, with ownership checked before role so that a cross-workspace caller receives not-found rather than a role denial that discloses existence. Explicit system-scope identities SHALL read across workspaces; a non-system identity without an active workspace SHALL be denied and SHALL NOT degrade to a global query. The boundary SHALL NOT change successful response shapes, version pagination, content hashes, the diff algorithm, or the 422 behavior for a malformed `against` parameter, and the three endpoints SHALL remain read-only.

#### Scenario: Versions of an invisible dataset are not readable
- **WHEN** any of the three version read endpoints is called for a dataset that does not exist, is soft-deleted, or belongs to another workspace
- **THEN** the response SHALL be 404 with the same body for all three conditions, without echoing the dataset identifier; soft-deleted datasets' versions SHALL NOT remain listable or readable

#### Scenario: Listing for a visible dataset keeps pagination and empty semantics
- **WHEN** a viewer (or higher) of a visible dataset lists its versions with `page` and `page_size`
- **THEN** the response keeps the existing newest-first ordering, pagination, and `total` semantics; a visible dataset with no versions returns 200 with an empty list and `total` 0

#### Scenario: Detail for an invisible version is indistinguishable and echoes no ID
- **WHEN** a caller requests a version that does not exist, is soft-deleted, belongs to another workspace, or does not belong to the dataset in the request path
- **THEN** all conditions SHALL receive the same 404 response body, and the body SHALL NOT contain any version, dataset, or caller identifier

#### Scenario: Diff with an invisible target returns not-found, not a server error
- **WHEN** `against` names a version that does not exist, is soft-deleted, or belongs to another workspace or another dataset
- **THEN** the response SHALL be 404 with the same body as the endpoint's other not-found responses, without echoing any identifier; the API SHALL NOT return a 500

#### Scenario: Role floor applies after ownership
- **WHEN** an authenticated identity without a workspace role requests versions under a dataset that belongs to another workspace
- **THEN** the response SHALL be the same 404 as a missing dataset, not a role-based 403; a viewer-role member of the dataset's workspace receives the same successful response a higher role would receive

#### Scenario: Role floor inside the caller workspace
- **WHEN** an identity whose active workspace contains the dataset but whose workspace membership role is below viewer (or absent), with all referenced datasets and versions visible, calls any of the three version read endpoints
- **THEN** the response SHALL be 403 authorization denied and no version content SHALL be returned

#### Scenario: System scope reads across workspaces
- **WHEN** an explicit system-scope identity calls any of the three version read endpoints for versions in any workspace
- **THEN** the requests are processed under the endpoints' existing semantics without workspace restriction from this boundary

#### Scenario: Missing workspace non-system identity is denied
- **WHEN** a validly authenticated non-system identity without an active workspace calls any of the three version read endpoints
- **THEN** the request is denied and the response SHALL NOT fall back to an unscoped global query

#### Scenario: Boundary preserves read semantics and read-only guarantee
- **WHEN** version read requests succeed or are rejected by the boundary
- **THEN** no version, dataset, or item state is created, modified, or deleted; a malformed `against` value that is neither a version id nor `live` SHALL keep its existing 422 response; content hashes and the diff classification algorithm remain unchanged


#### Scenario: Both diff sides are validated before role
- **WHEN** an identity without a workspace role requests a diff with a visible source and an invisible target, or an invisible source and a visible target
- **THEN** the response SHALL be the same identifier-free version 404 as a missing version, regardless of which side is invisible; the endpoint SHALL check all referenced resources before returning a role-based 403
