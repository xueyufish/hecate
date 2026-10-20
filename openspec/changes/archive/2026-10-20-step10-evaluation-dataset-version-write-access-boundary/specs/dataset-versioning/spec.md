# Spec Delta

## ADDED Requirements

### Requirement: Dataset version write access boundary

The version write endpoints (`POST /api/evaluation/datasets/{dataset_id}/versions`, `DELETE .../versions/{version_id}`, `POST .../versions/{version_id}/checkout`) SHALL enforce one shared write access boundary with a minimum `editor` workspace role. Ownership SHALL be decided before role: the parent dataset SHALL be resolved through the dataset read boundary first, so a dataset that does not exist, is soft-deleted, or belongs to another workspace SHALL produce the same not-found (404) response on all three endpoints without echoing any resource identifier; a version SHALL then be writable only when it exists, is not soft-deleted, belongs to the dataset in the request path, and has the same workspace as that dataset (including for system-scope callers), with all version-level failures sharing one constant identifier-free 404. All referenced resources SHALL be resolved before the editor role check. An authenticated non-system identity whose workspace membership role is below editor (or absent) SHALL receive 403 on a visible resource; a non-system identity without an active workspace SHALL be denied and SHALL NOT degrade to a global operation or a zero-UUID attribution. Explicit system-scope identities SHALL keep cross-workspace write capability, and every write SHALL be attributed to the target dataset's real workspace. Every rejected request SHALL leave versions, live items, and soft-delete markers unchanged, and the boundary SHALL NOT change successful response shapes, frozen content, content hashes, the duplicate-name 409, soft-delete semantics, or the checkout algorithm.

#### Scenario: Viewer in the workspace is denied with no side effects
- **WHEN** a viewer-role or role-less identity in the dataset's workspace calls create, delete, or checkout on visible, correctly bound resources
- **THEN** the response SHALL be 403 authorization denied, and no version, live item, or soft-delete marker SHALL change

#### Scenario: Editor and admin proceed with unchanged semantics
- **WHEN** an editor- (or admin-) role member of the dataset's workspace creates, deletes, or checks out a version of a visible dataset
- **THEN** the requests succeed with the existing response shapes: freezing keeps the frozen items and content hash, delete keeps soft-delete and name reservation, checkout keeps its diff summary and restoration algorithm

#### Scenario: Ownership is decided before role
- **WHEN** an authenticated identity without a workspace role requests a write against a dataset that belongs to another workspace
- **THEN** the response SHALL be the same identifier-free 404 as a missing dataset, not a role-based 403 that discloses existence

#### Scenario: Invisible or mismatched version on delete and checkout
- **WHEN** delete or checkout names a version that does not exist, is soft-deleted, belongs to a different workspace than its parent dataset, or is bound to another dataset, regardless of whether the caller is editor, viewer, role-less, or system
- **THEN** the response SHALL be the same identifier-free 404 on both endpoints, and no state SHALL change

#### Scenario: System scope writes across workspaces with real attribution
- **WHEN** an explicit system-scope identity creates, deletes, or checks out a version of a dataset in any workspace
- **THEN** the write SHALL proceed under the existing semantics and SHALL be attributed to the target dataset's real workspace

#### Scenario: Missing-workspace non-system identity cannot create
- **WHEN** a validly authenticated non-system identity without an active workspace calls create on any dataset
- **THEN** the request SHALL be denied, and no version SHALL be created with a zero-UUID or empty attribution

#### Scenario: Boundary preserves successful write semantics
- **WHEN** write requests succeed or are rejected by the boundary
- **THEN** frozen content and content hashes, the duplicate-name 409, soft-delete semantics, the checkout algorithm and response, and the read endpoints' behavior SHALL remain unchanged

#### Scenario: System rejects inconsistent legacy version attribution
- **WHEN** a system-scope identity deletes or checks out a version whose workspace differs from the parent dataset's workspace, including a legacy zero-UUID attribution
- **THEN** the response SHALL be the constant identifier-free version 404 and the dataset, version, and live items SHALL remain unchanged

#### Scenario: Version disappears after write authorization
- **WHEN** the version service reports a missing version during delete or checkout after the router precheck has succeeded
- **THEN** the response SHALL be the same constant identifier-free version 404 rather than an unhandled server error
