## Purpose

Automated backflow of scored production traces into evaluation datasets: a persisted backflow rule selects traces whose machine scores match configured filters (e.g. low-score failures) from an online evaluation task, and a triggered batch run materializes them as self-contained dataset items — no human per-item involvement. Complements the human-verified backflow path (annotation queues) and inherits its idempotency contract.

## ADDED Requirements

### Requirement: Backflow rule CRUD and validation
The system SHALL provide create, list, get, update, and delete operations for backflow rules. A rule SHALL contain: `name` (non-empty, unique per workspace), `task_id` (SHALL reference an existing **online** evaluation task in the same workspace), `dataset_id` (SHALL reference an existing evaluation dataset in the same workspace), one or more score filters (each: `metric_name`, optional `min_score`, optional `max_score`; multiple filters combine with AND semantics), `limit` (positive integer, default 500, hard cap per run), and optional `max_turns` (positive integer bounding multi-turn projection). The API SHALL reject at create/update time: an unknown `task_id` or `dataset_id`, a task whose `task_type` is not `online`, a filter with `min_score > max_score`, a non-positive `limit` or `max_turns`, and an empty filter list. The system SHALL NOT automatically trigger rules; triggering is always an explicit run request.

#### Scenario: Create a low-score backflow rule
- **WHEN** `POST /api/evaluation/backflow-rules` is called with `{"name": "low-score-harvest", "task_id": "<online-task>", "dataset_id": "<dataset>", "filters": [{"metric_name": "correctness", "max_score": 0.5}], "limit": 200}`
- **THEN** the rule SHALL be persisted referencing the online task and dataset, and SHALL be returned with a generated id

#### Scenario: Reject rule pointing at an offline task
- **WHEN** a rule is created with a `task_id` referencing an offline task
- **THEN** the API SHALL return a validation error and SHALL NOT persist the rule

#### Scenario: Reject inverted score band
- **WHEN** a filter declares `min_score: 0.8, max_score: 0.3`
- **THEN** the API SHALL return a validation error naming the offending filter

### Requirement: Batch materialization run
The system SHALL expose `POST /api/evaluation/backflow-rules/{id}/run` which selects scored traces of the rule's task whose per-metric scores satisfy ALL of the rule's filters (each filter matches the latest score for that metric on that trace), orders candidates deterministically (scored time ascending), and materializes at most `limit` items per run. Candidates are walked oldest-first; skips (already-materialized, unprojectable) do not consume the limit, so matches that surface after earlier runs remain reachable. For each candidate the system SHALL project the trace's session window into dataset-item content and persist a new item in the rule's dataset; candidates whose projection yields no complete user→assistant exchange SHALL be skipped without failing the run. The response SHALL return `{created, skipped, dataset_id}`. A run against a task with no matching scores SHALL complete successfully with `created: 0`.

#### Scenario: Materialize low-score traces
- **WHEN** a rule with filter `correctness <= 0.5` runs against a task that has scored 30 traces, 12 of which have a latest correctness score at or below 0.5
- **THEN** the run SHALL create up to 12 items (fewer if some projections fail or duplicates exist) and return the created/skipped counts

#### Scenario: Limit caps a single run
- **WHEN** a rule with `limit: 100` runs while 400 traces match its filters
- **THEN** at most 100 items SHALL be materialized in this run; the remaining matches remain eligible for subsequent runs

#### Scenario: Run with no matching scores
- **WHEN** the rule's filters match no scored traces
- **THEN** the run SHALL return `{created: 0, skipped: 0}` and SHALL NOT be an error

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
Backflow rules SHALL be scoped to a workspace: listing and reading SHALL only return rules in the caller's workspace, and running a rule SHALL only materialize into datasets and select traces within the rule's workspace. Cross-workspace access SHALL be rejected as not found.

#### Scenario: Rule invisible across workspaces
- **WHEN** a caller from workspace B requests a rule created in workspace A
- **THEN** the API SHALL return not found rather than the rule
