## Purpose
Define named, immutable evaluation-dataset versions — freezing a dataset's
live items under a unique name with a content hash shared with run dataset
snapshots, restoring them onto the live dataset via checkout, and diffing
any version against another version or the live dataset.

## Requirements

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
