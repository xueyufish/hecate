# Spec Delta

## ADDED Requirements

### Requirement: Session report access boundary

`GET /api/evaluation/reports/sessions` SHALL enforce one access boundary with two modes. Without `task_id`, the endpoint SHALL apply the workspace-aggregate gate: a caller without an active workspace — including explicit system-scope identities — SHALL receive 403 `NO_WORKSPACE` before any query, and a workspace member whose role is below viewer (or absent) SHALL receive 403 `FORBIDDEN`; the service SHALL receive the verified active workspace. With `task_id`, the endpoint SHALL resolve the referenced evaluation task before any role decision: a task that does not exist, is soft-deleted, or belongs to another workspace SHALL produce one constant identifier-free 404 shared by all conditions — and a non-system identity without an active workspace SHALL receive that same 404 before any query — while a member of the task's workspace whose role is below viewer (or absent) SHALL receive 403 `FORBIDDEN`. Explicit system-scope identities SHALL keep cross-workspace reads, and the service SHALL be invoked with the resolved task's real workspace for task-scoped reads (system callers SHALL receive the task's real rollup rather than an empty result) and with the verified active workspace for workspace-scoped reads. The boundary SHALL NOT modify any data and SHALL NOT change session aggregation, reconciliation, newest-scored-first ordering, pagination, time-window filtering, or successful response shapes.

#### Scenario: Workspace-scoped rollup honors the viewer floor
- **WHEN** a viewer-role member of a workspace requests sessions without `task_id`
- **THEN** the response SHALL be 200 with that workspace's session rollup, unchanged in shape, ordering, and pagination

#### Scenario: Workspace-scoped rollup without a workspace is 403 NO_WORKSPACE
- **WHEN** a caller without an active workspace — including an explicit system-scope identity — requests sessions without `task_id`
- **THEN** the response SHALL be 403 with error code `NO_WORKSPACE` before any query runs

#### Scenario: Task-scoped invisible resources share one identifier-free 404
- **WHEN** a caller requests sessions with a `task_id` that does not exist, is soft-deleted, or belongs to another workspace
- **THEN** the response SHALL be 404 with one constant body for all conditions, and the body SHALL NOT contain the task or caller identifier

#### Scenario: Task-scoped role floor applies after ownership
- **WHEN** a viewer-or-higher or role-less member of the task's workspace requests sessions for a visible task
- **THEN** a viewer-or-higher identity receives 200 with the task's session rollup, and an identity below viewer receives 403 `FORBIDDEN`

#### Scenario: System scope reads the task's real rollup
- **WHEN** an explicit system-scope identity requests sessions for a task in any workspace
- **THEN** the response SHALL be 200 with that task's real session rollup, and the service SHALL be invoked with the task's workspace rather than a null or global filter

#### Scenario: Boundary preserves aggregation semantics and read-only guarantee
- **WHEN** requests succeed or are rejected by the boundary
- **THEN** no data SHALL be created, modified, or deleted; session aggregation, reconciliation, ordering, pagination, and time-window filters remain unchanged; a valid workspace or task with no scored sessions keeps returning its normal empty result

#### Scenario: Ownership is checked before the role for foreign callers
- **WHEN** an identity without a workspace role requests sessions for a task in another workspace
- **THEN** the response SHALL be the same identifier-free 404 as a missing task, rather than a role-based 403, and the aggregation service SHALL NOT run

#### Scenario: System cannot read a deleted task with residual scores
- **WHEN** an explicit system-scope identity requests sessions for a missing or soft-deleted task, including a deleted task with remaining scores
- **THEN** the response SHALL be the same identifier-free 404, and the aggregation service SHALL NOT run
