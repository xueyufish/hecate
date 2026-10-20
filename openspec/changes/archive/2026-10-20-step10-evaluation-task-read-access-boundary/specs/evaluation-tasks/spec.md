# Spec Delta — evaluation-tasks

## ADDED Requirements

### Requirement: Evaluation task read access boundary

The task listing, detail, and run-listing endpoints (`GET /api/evaluation/tasks`, `GET /api/evaluation/tasks/{task_id}`, `GET /api/evaluation/tasks/{task_id}/runs`) SHALL enforce one shared read access boundary: an authenticated non-system identity SHALL only observe evaluation tasks belonging to its active workspace, with a minimum viewer role in that workspace; the listing's items and `total` SHALL count only tasks within the caller's workspace. A task SHALL be readable only when it exists, is not soft-deleted, and belongs to the caller's workspace; a task failing any of these conditions SHALL produce a 404 whose body is identical for all conditions and SHALL NOT echo any resource identifier, and the runs of an invisible task SHALL NOT be listable. The task's run listing SHALL include only runs bound to that task AND carrying the task's workspace, excluding soft-deleted runs; `items` and `total` SHALL apply the same conditions. Ownership SHALL be checked before role so that a cross-workspace caller receives not-found rather than a role denial that discloses existence. Explicit system-scope identities SHALL read across workspaces — their `workspace_id=None` SHALL NOT be used as an equality query condition — and when a system-scope identity reads a task's runs, the runs SHALL be constrained to that task's real workspace. A non-system identity without an active workspace SHALL be denied and SHALL NOT reach an unscoped query. The boundary SHALL NOT change successful response shapes, pagination, task type/status filters, ordering, the task write endpoints' behavior, or the `get_task`/`_get_task_or_raise` scope semantics shared with writes, and the three endpoints SHALL remain read-only.

#### Scenario: Listing is scoped to the caller workspace
- **WHEN** tasks exist in workspaces W1 and W2 and a W1 member calls `GET /api/evaluation/tasks`
- **THEN** only W1 tasks are returned and `total` counts only W1 tasks

#### Scenario: System scope reads across workspaces
- **WHEN** an explicit system-scope identity calls the task listing for tasks in any workspace, or reads a specific task and its runs in any workspace
- **THEN** the requests are processed under the endpoints' existing semantics without workspace restriction from this boundary; the system identity's null workspace SHALL NOT be applied as an equality condition that yields an empty list or not-found

#### Scenario: Missing workspace non-system identity is denied
- **WHEN** a validly authenticated non-system identity without an active workspace calls any of the three task read endpoints
- **THEN** the request is denied and the response SHALL NOT fall back to an unscoped query

#### Scenario: Detail for an invisible task is indistinguishable and echoes no ID
- **WHEN** a caller requests a task that does not exist, is soft-deleted, or belongs to another workspace
- **THEN** all conditions SHALL receive the same 404 response body without echoing any task, workspace, or caller identifier

#### Scenario: Runs of an invisible task are not listable
- **WHEN** a caller requests the run listing of a task that does not exist, is soft-deleted, or belongs to another workspace
- **THEN** the response SHALL be 404 with the same body for all conditions, without echoing any identifier

#### Scenario: Run listing is constrained to the task's real workspace
- **WHEN** a run row references the task but carries a different workspace than the task, alongside runs correctly bound to the task
- **THEN** the mismatched-workspace run SHALL NOT appear in `items` and SHALL NOT be counted in `total`

#### Scenario: Role floor applies after ownership
- **WHEN** an authenticated identity without a workspace role requests a task that belongs to another workspace
- **THEN** the response SHALL be the same 404 as a missing task, not a role-based 403; a viewer-role member of the task's workspace receives the same successful response a higher role would receive

#### Scenario: Role floor inside the caller workspace
- **WHEN** an identity whose active workspace contains the task but whose workspace membership role is below viewer (or absent) calls any of the three task read endpoints
- **THEN** the response SHALL be 403 authorization denied and no task or run content SHALL be returned

#### Scenario: Boundary preserves read semantics, write behavior, and read-only guarantee
- **WHEN** task read requests succeed or are rejected by the boundary
- **THEN** no task or run state is created, modified, or deleted; pagination, task type/status filters, and ordering keep their existing behavior; the task write endpoints' behavior and the shared `get_task`/`_get_task_or_raise` scope semantics remain unchanged
