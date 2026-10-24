# Spec Delta — dataset-synthesis

## ADDED Requirements

### Requirement: Synthesis access boundary

The synthesis kickoff endpoint (`POST /api/evaluation/datasets/synthesize`) and the job polling endpoint (`GET /api/evaluation/synthesis-jobs/{job_id}`) SHALL enforce one shared access boundary anchored to a server-validated target workspace. On kickoff, a non-system identity SHALL resolve the target workspace to its own active workspace: a request without an active workspace SHALL be denied with 403 `NO_WORKSPACE` before any query, and a request body `workspace_id` that is neither omitted nor equal to the caller's active workspace SHALL produce a constant 403 FORBIDDEN regardless of whether the named workspace exists. Explicit system-scope identities SHALL keep the existing workspace-selection field and MAY target any existing, non-deleted workspace; a kickoff without a resolvable target workspace — including a selection naming a missing or deleted workspace — SHALL be denied with 403 `NO_WORKSPACE`. Synthesis kickoff SHALL additionally require a minimum editor role in the target workspace for non-system identities. When `seed_dataset_id` is supplied, the seed dataset SHALL be resolved before the role check and SHALL only be usable when it exists, is not soft-deleted, and belongs to the target workspace; every failing condition SHALL produce the dataset endpoints' constant identifier-free 404; seed eligibility SHALL be anchored to the parent dataset so historical items whose own workspace values differ (including the zero UUID) remain valid seeds. Every boundary rejection SHALL occur before any target dataset, dataset item, or job row is created, before any background task is scheduled, and before any model call. The pipeline SHALL re-validate the seed dataset at execution time — for the asynchronous job and as a defense-in-depth check on the synchronous path — before creating the target dataset, reading seed items, or calling the model; a seed that became missing, soft-deleted, or ineligible after acceptance SHALL fail the job explicitly (no topic fallback, no target dataset, no model call), while the synchronous path SHALL map seed unavailability to the same constant 404. The job polling endpoint SHALL resolve the job first: a job that does not exist, is soft-deleted, or belongs to another workspace SHALL produce a constant identifier-free 404, a non-system identity without an active workspace SHALL receive the same 404 before any query, and a visible job SHALL then require a minimum viewer role for non-system identities; explicit system-scope identities SHALL poll jobs across workspaces by job ID. The boundary SHALL NOT change the synchronous/asynchronous split (`count <= 20` → 200, `count > 20` → 202), successful response shapes, generation strategies, the filter pipeline, provenance tags, `422` validation of malformed requests, or the job status lifecycle, and the polling endpoint SHALL remain read-only.

#### Scenario: Non-system kickoff is anchored to the caller workspace

- **WHEN** a member of workspace W calls the synthesis endpoint with `workspace_id` omitted, equal to W, or naming another workspace
- **THEN** omitted and self-referencing values proceed to the role check, while any foreign `workspace_id` value SHALL receive the same constant 403 FORBIDDEN whether or not that workspace exists, and no resource SHALL be created

#### Scenario: Kickoff requires a resolvable target workspace

- **WHEN** a non-system identity without an active workspace calls the synthesis endpoint, or a system-scope identity calls it without a workspace selection or with a selection naming a missing or soft-deleted workspace
- **THEN** the response SHALL be 403 with the `NO_WORKSPACE` code before any dataset, item, or job row is created; no synthesized resource SHALL fall back to the historical zero-UUID owner

#### Scenario: System kickoff lands in the explicitly selected workspace

- **WHEN** an explicit system-scope identity kicks off synthesis selecting an existing, non-deleted workspace
- **THEN** the created target dataset, its items, and any asynchronous job row SHALL carry that workspace as their `workspace_id`

#### Scenario: Editor role floor applies to synthesis kickoff

- **WHEN** a non-system identity whose active workspace is the target but whose workspace membership role is below editor (including viewer and role-less members) calls the synthesis endpoint
- **THEN** the response SHALL be 403 authorization denied and no target dataset, item, or job SHALL be created; an editor-role member receives the same successful response an admin would receive

#### Scenario: Seed dataset visibility precedes the role check

- **WHEN** a caller supplies a `seed_dataset_id` naming a dataset that does not exist, is soft-deleted, or belongs to a workspace other than the resolved target, regardless of the caller's role in the target workspace
- **THEN** the response SHALL be the dataset endpoints' constant identifier-free 404, identical for all conditions and echoing no identifier; the role check SHALL NOT run first so ownership is never disclosed through a role denial

#### Scenario: Seed eligibility is anchored to the parent dataset

- **WHEN** a valid seed dataset contains items whose own historical `workspace_id` values differ from the dataset's workspace, including zero-UUID rows
- **THEN** every non-deleted item of the seed dataset SHALL be read as seed material; the items' own workspace values SHALL NOT disqualify legitimate seeds

#### Scenario: Boundary rejection precedes every side effect

- **WHEN** a synthesis kickoff is rejected by the workspace resolution, seed validation, or role check
- **THEN** no target dataset, dataset item, or job row SHALL be created, no background task SHALL be scheduled, and no model SHALL be invoked

#### Scenario: Pipeline re-validates a seed invalidated after acceptance

- **WHEN** an accepted asynchronous job's seed dataset becomes missing or soft-deleted (or otherwise ineligible) before the pipeline runs
- **THEN** the job SHALL transition to `failed` with an explicit error, SHALL NOT fall back to the topic, SHALL NOT create a target dataset, and SHALL NOT call the model; the synchronous path SHALL map the same seed unavailability to the constant identifier-free dataset 404

#### Scenario: Job polling follows visibility before role

- **WHEN** a caller requests a job that does not exist, is soft-deleted, or belongs to another workspace, or a non-system identity without an active workspace requests any job
- **THEN** all conditions SHALL receive the same identifier-free 404, with the missing-workspace case rejected before any query; a visible job requested by a role-less or below-viewer member of its workspace SHALL instead produce 403 authorization denied

#### Scenario: System scope polls jobs across workspaces

- **WHEN** an explicit system-scope identity requests a job by ID in any workspace
- **THEN** the request succeeds under the endpoint's existing semantics without workspace restriction from this boundary

#### Scenario: Boundary preserves synthesis semantics

- **WHEN** synthesis kickoffs succeed or are rejected by the boundary
- **THEN** the synchronous/asynchronous split (200 with the result summary for `count <= 20`, 202 with `job_id` and `status="queued"` for `count > 20`), successful response shapes, generation strategies, the filter pipeline, provenance tags, `422` behavior for malformed requests, and the job status lifecycle remain unchanged, and the polling endpoint creates, modifies, and deletes nothing
