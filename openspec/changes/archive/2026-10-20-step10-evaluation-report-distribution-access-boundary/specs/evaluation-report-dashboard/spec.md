# Spec Delta

## ADDED Requirements

### Requirement: Distributions report access boundary

`GET /api/evaluation/reports/distributions` SHALL enforce one access boundary for both scopes. The request SHALL first keep its existing parameter contract: a malformed UUID stays a 422, and providing both or neither of `run_id`/`task_id` stays a 400 with the existing message. When exactly one scope is supplied, the referenced Run or evaluation task SHALL be resolved before any role decision: a resource that does not exist, is soft-deleted, or belongs to another workspace SHALL produce the same constant not-found (404) response across both Run and evaluation-task scopes whose body SHALL NOT echo any resource identifier. The caller SHALL then hold a minimum `viewer` role in the resource's workspace; a role-less member of a visible resource's workspace SHALL receive 403, and a non-system identity without an active workspace SHALL be denied before resource lookup or aggregation. Explicit system-scope identities SHALL keep cross-workspace reads, and the service call SHALL use the resolved resource's real workspace — the boundary SHALL NOT introduce a global aggregation query, and system callers SHALL receive the resource's real histograms rather than an empty result. The boundary SHALL NOT modify any data and SHALL NOT change histogram binning, error-score exclusion, human-override reconciliation, or successful response shapes.

#### Scenario: Run scope honors the viewer floor
- **WHEN** a viewer-role member of a run's workspace requests distributions for that run
- **THEN** the response SHALL be 200 with the run's per-metric histograms, unchanged in shape

#### Scenario: Task scope honors the viewer floor
- **WHEN** a viewer-role member of an evaluation task's workspace requests distributions for that task
- **THEN** the response SHALL be 200 with reconciled online histograms, unchanged in shape

#### Scenario: Role-less member of a visible resource is denied
- **WHEN** a membership identity without a role requests distributions for a run or task in its own workspace
- **THEN** the response SHALL be 403 authorization denied and no aggregation SHALL run

#### Scenario: Invisible resources share one identifier-free 404
- **WHEN** the requested run or task does not exist, is soft-deleted, or belongs to another workspace
- **THEN** the response SHALL be 404 with one constant body for all conditions, and the body SHALL NOT contain the run, task, or caller identifier

#### Scenario: System scope reads with the resource's real workspace
- **WHEN** an explicit system-scope identity requests distributions for a run or task in any workspace
- **THEN** the response SHALL be 200 with that resource's real histograms, and the service SHALL be invoked with the resource's workspace rather than a global or null filter

#### Scenario: Missing-workspace non-system identity is denied
- **WHEN** a validly authenticated non-system identity without an active workspace calls the endpoint with exactly one valid scope
- **THEN** the response SHALL be the constant identifier-free 404 and no resource lookup or aggregation query SHALL run

#### Scenario: Parameter contract and read-only guarantee are preserved
- **WHEN** a request supplies both `run_id` and `task_id`, or neither, or a malformed UUID
- **THEN** the responses keep the existing 400 messages and 422 respectively; successful and rejected requests SHALL NOT create, modify, or delete any data, and binning, error-score exclusion, and reconciliation semantics remain unchanged

#### Scenario: Resource disappears after the ownership precheck
- **WHEN** the report service reports that a scope is not found after the router has authorized that scope
- **THEN** the endpoint SHALL return the same constant identifier-free 404 body used for a failed resource precheck, for both Run and evaluation-task scopes
