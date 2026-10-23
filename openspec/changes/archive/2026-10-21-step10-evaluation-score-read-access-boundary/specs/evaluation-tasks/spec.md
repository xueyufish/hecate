# Spec Delta — evaluation-tasks

## ADDED Requirements

### Requirement: Evaluation score read access boundary

The score query endpoint (`GET /api/evaluation/scores`) SHALL enforce one shared read access boundary. A request without `task_id` SHALL be a workspace-scoped query: every identity without an active workspace — including explicit system scope — SHALL be denied with 403 `NO_WORKSPACE` before any query, and a non-system identity with an active workspace SHALL hold a minimum viewer role in that workspace; explicit system scope SHALL retain its role exemption when an active workspace is supplied. A request with `task_id` SHALL resolve the task through the task read boundary first: a task that does not exist, is soft-deleted, or is invisible to the caller SHALL produce the task endpoints' constant identifier-free 404 — not an empty successful list — with ownership checked before a minimum viewer role is enforced for non-system identities; a non-system identity without an active workspace SHALL receive the same 404 before any query. Explicit system-scope identities SHALL read across workspaces, and the score query SHALL then be constrained to the task's real workspace. Score queries SHALL apply the task/workspace condition, the not-deleted condition, and every requested filter to both `items` and `total`. The boundary SHALL NOT change successful response shapes, pagination, `created_at` ascending ordering, the existing filter parameters (`target_id`, `session_id`, `metric_name`, `status`, `source`), the ability to query manually annotated rows (`task_id` NULL rows returned when `task_id` is omitted, `source=human` supported), or the ability to read scores of a disabled task, and the endpoint SHALL remain read-only.

#### Scenario: Query without task_id is scoped to the caller workspace
- **WHEN** score rows exist under tasks in workspaces W1 and W2 and a W1 viewer calls `GET /api/evaluation/scores` without `task_id`
- **THEN** only W1 scores are returned and `total` counts only W1 scores

#### Scenario: Workspace-scoped queries require an active workspace
- **WHEN** any identity — including explicit system scope — calls the score endpoint without task_id and without an active workspace
- **THEN** the response SHALL be 403 with the `NO_WORKSPACE` code before any query; the system identity's null workspace SHALL NOT be applied as an equality condition that silently yields an empty list

#### Scenario: Role floor inside the caller workspace
- **WHEN** a non-system identity whose active workspace contains scores but whose workspace membership role is below viewer (or absent) calls the score endpoint
- **THEN** the response SHALL be 403 authorization denied and no score content SHALL be returned

#### Scenario: Scores of an invisible task return not-found, not an empty list
- **WHEN** `task_id` names a task that does not exist, is soft-deleted, or is invisible to the caller
- **THEN** the response SHALL be the task endpoints' constant identifier-free 404 with the same body for all conditions; the API SHALL NOT return an empty successful list in place of the resource visibility denial

#### Scenario: System scope reads a visible task's scores across workspaces
- **WHEN** an explicit system-scope identity queries scores with `task_id` naming a task in any workspace
- **THEN** the request succeeds and the scores SHALL be constrained to that task's real workspace

#### Scenario: Boundary preserves query semantics and read-only guarantee
- **WHEN** score queries succeed or are rejected by the boundary
- **THEN** no score or task state is created, modified, or deleted; pagination, `created_at` ascending ordering, the existing filter parameters, manual-annotation queries (`task_id` NULL rows when `task_id` is omitted, `source=human`), disabled-task score reads, and response shapes remain unchanged

#### Scenario: Missing workspace non-system task query is denied before lookup
- **WHEN** an authenticated non-system identity without an active workspace calls the score endpoint with task_id
- **THEN** the response SHALL be the task endpoints' constant identifier-free 404 before any resource or score query

#### Scenario: Task-scoped role check follows visibility
- **WHEN** a role-less non-system identity queries a visible task in its own workspace or an invisible task in another workspace
- **THEN** the visible task SHALL produce 403 FORBIDDEN and the invisible task SHALL produce the same 404 as a missing task, with no score query in either case
