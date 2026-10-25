# Capability: trace-backflow

> Synced from archive of change `automated-trace-backflow` (7.2d).
> Automated backflow of scored production traces into evaluation datasets: a
> persisted backflow rule selects traces whose machine scores match
> configured score-band filters from an online evaluation task, and an
> explicitly triggered batch run materializes them as self-contained dataset
> items — no human per-item involvement. Complements the human-verified
> backflow path (annotation queues) and shares dataset-wide trace
> idempotency with it.

## Purpose

Continuously convert production scoring results into regression-corpus
material: rules harvest scored traces (e.g. low-score failures) into
evaluation datasets without a human in the loop, so dataset growth keeps
pace with production traffic while machine scores stay provenance — never
ground truth.

## Requirements

### Requirement: Backflow rule CRUD and validation
The system SHALL provide create, list, get, update, and delete operations for backflow rules. A rule SHALL contain: `name` (non-empty, unique per workspace), `task_id` (SHALL reference an existing **online** evaluation task in the same workspace), `dataset_id` (SHALL reference an existing evaluation dataset in the same workspace), one or more score filters (each: `metric_name`, optional `min_score`, optional `max_score`; multiple filters combine with AND semantics), `limit` (positive integer, default 500, hard cap per run), and optional `max_turns` (positive integer bounding multi-turn projection). The API SHALL reject at create/update time: an unknown `task_id` or `dataset_id`, a task whose `task_type` is not `online`, a filter with `min_score > max_score`, a non-positive `limit` or `max_turns`, and an empty filter list. The system SHALL NOT automatically trigger rules; triggering is always an explicit run request.

Rule creation and listing SHALL sit behind the evaluation access boundary: every identity — system scope included — SHALL hold an active workspace, denied with 403 `NO_WORKSPACE` before any query; non-system callers SHALL hold at least an editor role to create and at least a viewer role to list, and system scope SHALL NOT gain a global listing. References SHALL resolve like resources: create SHALL validate the requested task and dataset, and update SHALL validate the merged-patch effective task and dataset pair — each live in the target workspace before the role check and any business validation, system scope included, so cross-workspace stitching is impossible. An invisible rule or reference on create or update SHALL return the combined identifier-free `Backflow rule or task or dataset not found` 404; detail and delete SHALL keep the rule-only 404 message, and deletion SHALL NOT depend on the rule's references still being alive so rules pointing at removed objects stay cleanable. Business validation (online task type, filter bands, name uniqueness) SHALL keep its 400 after authorization, and request-schema violations SHALL keep 422.

#### Scenario: Create a low-score backflow rule
- **WHEN** `POST /api/evaluation/backflow-rules` is called with `{"name": "low-score-harvest", "task_id": "<online-task>", "dataset_id": "<dataset>", "filters": [{"metric_name": "correctness", "max_score": 0.5}], "limit": 200}`
- **THEN** the rule SHALL be persisted referencing the online task and dataset, and SHALL be returned with a generated id

#### Scenario: Reject rule pointing at an offline task
- **WHEN** a rule is created with a `task_id` referencing an offline task
- **THEN** the API SHALL return a validation error and SHALL NOT persist the rule

#### Scenario: Reject inverted score band
- **WHEN** a filter declares `min_score: 0.8, max_score: 0.3`
- **THEN** the API SHALL return a validation error naming the offending filter

#### Scenario: Reference visibility precedes the role check
- **WHEN** a viewer creates or updates a rule whose effective task or dataset is missing, soft-deleted, or owned by another workspace
- **THEN** the API SHALL return the combined identifier-free 404 before the role check and before any business validation, and an editor hitting the same invisible reference SHALL receive the same 404

#### Scenario: Active workspace is required to create or list
- **WHEN** any identity — system scope included — without an active workspace creates or lists backflow rules
- **THEN** the response SHALL be 403 `NO_WORKSPACE` before any query, and denied requests SHALL NOT read or write rules, tasks, datasets, samples, or scores

#### Scenario: Delete never depends on reference liveness
- **WHEN** a rule's bound task or dataset has been removed and an editor deletes the rule
- **THEN** the deletion SHALL succeed, subsequent reads SHALL return the rule-only 404, and already-materialized dataset items SHALL be retained

#### Scenario: Updates require an online effective task
- **WHEN** an authorized caller updates a rule whose effective task is offline, including when the patch leaves `task_id` unchanged
- **THEN** the update SHALL return 400 before changing the rule, while unauthorized callers SHALL still receive their role denial before task-type validation

#### Scenario: Concurrent rebinding does not replace the admitted update target
- **WHEN** a rule's stored task or dataset binding changes after API admission and before the update service reads it, even to another live resource in the same workspace
- **THEN** the update SHALL return the combined identifier-free 404 before modifying the rule; an explicit reference change in the patch SHALL remain allowed when the stored binding itself has not changed

### Requirement: Batch materialization run
The system SHALL expose `POST /api/evaluation/backflow-rules/{id}/run` which selects scored traces of the rule's task whose per-metric scores satisfy ALL of the rule's filters (each filter matches the latest score for that metric on that trace), orders candidates deterministically (scored time ascending, target id ascending on ties), and materializes at most `limit` items per run. Candidates are walked oldest-first; skips (already-materialized, unprojectable) do not consume the limit, so matches that surface after earlier runs remain reachable. For each candidate the system SHALL project the trace's session window into dataset-item content and persist a new item in the rule's dataset; candidates whose projection yields no complete user→assistant exchange SHALL be skipped without failing the run. The response SHALL return `{created, skipped, dataset_id}`. A run against a task with no matching scores SHALL complete successfully with `created: 0`.

The run SHALL sit behind the evaluation write boundary: the rule and its currently bound task and dataset SHALL resolve first — each live in the rule's real workspace before the role check, with an invisible rule or reference returning the combined identifier-free 404 — then non-system callers SHALL hold at least an editor role. A workspace-less non-system identity SHALL be denied before any query. Explicit system scope SHALL keep its role exemption, run the rule by id in the rule's real workspace, and SHALL NOT combine a rule with references from other workspaces. The service SHALL recheck the rule and its bound references after the API precheck — a rule, task, or dataset deleted or rebound in between SHALL reject the run with the same 404, leaving no samples and no `last_run` summary — and a rejected run SHALL NOT read candidate scores or trace events.

#### Scenario: Materialize low-score traces
- **WHEN** a rule with filter `correctness <= 0.5` runs against a task that has scored 30 traces, 12 of which have a latest correctness score at or below 0.5
- **THEN** the run SHALL create up to 12 items (fewer if some projections fail or duplicates exist) and return the created/skipped counts

#### Scenario: Limit caps a single run
- **WHEN** a rule with `limit: 100` runs while 400 traces match its filters
- **THEN** at most 100 items SHALL be materialized in this run; the remaining matches remain eligible for subsequent runs

#### Scenario: Run with no matching scores
- **WHEN** the rule's filters match no scored traces
- **THEN** the run SHALL return `{created: 0, skipped: 0}` and SHALL NOT be an error

#### Scenario: Stale bindings are rechecked before materializing
- **WHEN** the rule, its task, or its dataset is soft-deleted or rebound after the API precheck and before the service reads them
- **THEN** the run SHALL be rejected with the combined identifier-free 404, no dataset item SHALL be created, and the rule's `last_run` summary SHALL be unchanged

#### Scenario: Live replacement references cannot redirect an admitted run
- **WHEN** a rule is rebound to another live task or dataset in the same workspace after API admission and before the service reads it
- **THEN** the run SHALL return the combined identifier-free 404 without selecting candidates, creating samples, or updating `last_run`

#### Scenario: Task type remains an execution prerequisite
- **WHEN** an authorized caller runs a rule whose task is offline, including when the type changes after the API precheck
- **THEN** the service SHALL return 400 before selecting candidates or updating `last_run`; a disabled online task SHALL remain eligible for an explicit run

#### Scenario: Only qualifying scores become candidates or provenance
- **WHEN** the score ledger holds foreign-workspace, human-sourced, error-status, soft-deleted, or non-trace-typed rows alongside the qualifying ones for the rule's task
- **THEN** only same-workspace, same-task, trace-typed, completed, undeleted, non-human, non-sentinel rows SHALL satisfy filters and appear in `metadata_.backflow.scores`

#### Scenario: Ineligible traces never read events
- **WHEN** a candidate's target is a foreign, soft-deleted, non-root, or non-completed trace
- **THEN** the candidate SHALL be skipped before any event projection and no dataset item SHALL be created

#### Scenario: Samples carry the rule's real workspace
- **WHEN** a rule runs for a workspace with a non-zero id, or for the explicit zero-UUID workspace
- **THEN** every newly materialized item SHALL record that workspace explicitly rather than a default value

### Requirement: Idempotent materialization across backflow and annotation paths
A trace SHALL materialize into a given dataset at most once, regardless of which backflow path brought it in. Before creating an item, the run SHALL check the dataset for an existing item attributed to the same trace id — whether attributed via the automated path (`metadata_.backflow.trace_id`) or the human-annotation path (`metadata_.annotation.trace_id`) — and skip the candidate when found.

#### Scenario: Trace already materialized via annotation queue
- **WHEN** a trace was previously pushed into the dataset from an annotation queue and a backflow rule run selects the same trace
- **THEN** the candidate SHALL be counted as skipped and no duplicate item SHALL be created

#### Scenario: Re-running the same rule
- **WHEN** the same rule runs twice without new matching traces
- **THEN** the second run SHALL report `created: 0` with all previously materialized traces counted as skipped

### Requirement: Self-contained item content with multi-turn preservation
Each materialized item SHALL contain the projected `query` and `generated_answer` copied from the trace's session window (the item remains usable after the source trace or session events are purged). When the trace window contains multiple user→assistant exchanges, the item SHALL additionally preserve the conversation history (bounded by the rule's `max_turns`; windows exceeding the bound are skipped rather than truncated mid-conversation). `expected_answer` SHALL remain empty — machine scores are provenance, not ground truth.

#### Scenario: Single-turn trace materializes as a plain item
- **WHEN** a trace window contains exactly one user→assistant exchange
- **THEN** the item SHALL carry `query` and `generated_answer` from that exchange, empty `expected_answer`, and no conversation history

#### Scenario: Multi-turn window preserves history
- **WHEN** a trace window contains a three-turn conversation and the rule does not set `max_turns`
- **THEN** the item SHALL carry the final exchange as `query`/`generated_answer` and the preceding exchanges as conversation history on the item

#### Scenario: Oversize window is skipped
- **WHEN** a trace window's message count exceeds the projection cap
- **THEN** the candidate SHALL be skipped (counted in `skipped`) and SHALL NOT be truncated mid-conversation

### Requirement: Machine-score provenance on materialized items
Every materialized item SHALL record its provenance: `metadata_.backflow` SHALL contain the source `trace_id`, `task_id`, `rule_id`, and a snapshot of the scores that matched the rule's filters (metric name, value, source, scored time). The item SHALL carry `tags` including `trace-backflow` and the rule's `name`.

#### Scenario: Provenance recorded on materialization
- **WHEN** a trace with a correctness score of 0.3 is materialized by rule "low-score-harvest"
- **THEN** the item's metadata SHALL contain the trace id, task id, rule id, and the `{metric_name: "correctness", value: 0.3}` score snapshot, and its tags SHALL include `trace-backflow` and `low-score-harvest`

### Requirement: Workspace-scoped rule access
Backflow rules SHALL be scoped to a workspace. By-id reads, updates, deletes, and runs SHALL resolve the rule's existence, soft-delete state, and workspace ownership before the role check — missing, soft-deleted, and cross-workspace rules SHALL surface as one identifier-free not-found (the combined message on update and run, the rule-only message on detail and delete), and a workspace-less non-system identity SHALL be denied before any query. Reads SHALL require a viewer floor and writes (update, delete, run) an editor floor for non-system callers. Explicit system scope SHALL keep its role exemption: it MAY operate by explicit id without an active workspace and SHALL act in the rule's real workspace, but SHALL NOT gain a global listing or cross-workspace reference stitching. Cross-workspace access for non-system identities SHALL behave as if the rule does not exist.

#### Scenario: Rule invisible across workspaces
- **WHEN** a caller from workspace B requests a rule created in workspace A
- **THEN** the API SHALL return not found rather than the rule

#### Scenario: System scope operates through the real workspace
- **WHEN** an explicit system-scope identity reads, updates, deletes, or runs a live rule whose real workspace differs from its active workspace
- **THEN** the operations SHALL succeed in the rule's real workspace, while a missing or soft-deleted rule SHALL still be rejected and a foreign task or dataset reference SHALL never be stitched in
