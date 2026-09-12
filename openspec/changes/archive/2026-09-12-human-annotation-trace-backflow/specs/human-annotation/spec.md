## Purpose

Human annotation queues for reviewing production agent outputs: reviewers label and score traces in structured worklists, human scores land in the shared target-typed score ledger (coexisting with automated scores), explicit overrides calibrate automated judge scores with mandatory reasons, completed annotations flow back into evaluation datasets, and agreement analytics quantify judge-vs-human calibration.

## ADDED Requirements

### Requirement: Annotation queue definition and CRUD
The system SHALL provide CRUD endpoints at `/api/evaluation/annotation-queues`. A queue SHALL have a `name`, an optional `description`, optional `instructions` (free-text reviewer guidance), a non-empty list of `metric_defs`, and an optional list of `assigned_user_ids`. Each metric definition SHALL contain a `name` (unique within the queue), a `data_type` (`numeric` | `categorical` | `boolean`), and for `numeric` a `min`/`max` range or for `categorical` a non-empty `categories` list. Queues SHALL be scoped to the creating workspace and invisible to other workspaces.

#### Scenario: Create queue with metric definitions
- **WHEN** `POST /api/evaluation/annotation-queues` is called with `{"name": "weekly-qa-review", "instructions": "Rate helpfulness 0-1", "metric_defs": [{"name": "helpfulness", "data_type": "numeric", "min": 0, "max": 1}, {"name": "tone", "data_type": "categorical", "categories": ["good", "neutral", "bad"]}]}`
- **THEN** the queue SHALL be persisted and returned with its `id` and resolved metric definitions

#### Scenario: Reject queue without metric definitions
- **WHEN** a queue is created with an empty `metric_defs` list or an unknown `data_type`
- **THEN** the API SHALL return a validation error and SHALL NOT persist the queue

#### Scenario: Workspace isolation
- **WHEN** a queue belonging to workspace A is requested from workspace B
- **THEN** the API SHALL behave as if the queue does not exist

### Requirement: Queue item intake
The system SHALL support adding root traces as queue items via `POST /api/evaluation/annotation-queues/{queue_id}/items` (single or bulk, bulk capped at 100 per call) and via `POST /api/evaluation/annotation-queues/{queue_id}/items/from-task` with `{task_id, metric_name?, min_score?, max_score?, limit}` that selects matching online-task score records and enqueues their target traces (up to `limit`, default 50). A trace SHALL appear in a queue at most once: adding a trace already present SHALL be a no-op and the from-task flow SHALL skip already-enqueued traces. Adding a trace that does not exist as a completed root trace in the workspace SHALL be rejected. Queue items SHALL be created in `pending` state recording `added_by`.

#### Scenario: Bulk manual intake
- **WHEN** 3 trace IDs are posted to the items endpoint of a queue
- **THEN** 3 items SHALL be created in `pending` state and the response reports the created count

#### Scenario: From-task filtered intake
- **WHEN** `items/from-task` is called with `{task_id: T, max_score: 0.5, limit: 20}` and task T has 30 score records at or below 0.5 whose traces are not yet in the queue
- **THEN** 20 items SHALL be created for those traces and already-enqueued traces SHALL be skipped

#### Scenario: Duplicate add is a no-op
- **WHEN** a trace already present in the queue is added again
- **THEN** no duplicate item SHALL be created and the API reports the item as already present

#### Scenario: Reject unknown or non-root trace
- **WHEN** an item is added with a trace ID that does not exist, belongs to another workspace, or is a child span
- **THEN** the API SHALL return a validation error

### Requirement: Queue item workflow (claim / submit / skip)
A queue item SHALL move through `pending → claimed → completed | skipped`. `POST .../items/{item_id}/claim` SHALL record `claimed_by` and `claimed_at`; when the queue has a non-empty `assigned_user_ids`, only assigned users MAY claim, otherwise any workspace member MAY claim. `POST .../items/{item_id}/submit` SHALL carry one annotation entry per queue metric (`metric_name`, `value`, optional `overrides_score_id`, optional `reason_code`, optional `justification`), validate each `value` against the metric definition's domain, and persist the entries as human score records (see "Human scores in the shared ledger"); the item SHALL become `completed` with `completed_by`/`completed_at`. `POST .../items/{item_id}/skip` SHALL mark the item `skipped`. Submitting or claiming a completed or skipped item SHALL be rejected. Deleting an item SHALL only be allowed in `pending` or `skipped` state.

#### Scenario: Claim and submit complete an item
- **WHEN** an assigned reviewer claims a pending item, then submits `{"annotations": [{"metric_name": "helpfulness", "value": 0.8, "overrides_score_id": "...", "reason_code": "judge_too_harsh", "justification": "answer was correct"}]}`
- **THEN** the item SHALL be `completed` with the reviewer recorded, and a human score row for `helpfulness` SHALL exist for the item's trace

#### Scenario: Value outside metric domain is rejected
- **WHEN** a submit carries `value: 1.5` for a numeric metric with `max: 1`, or an unknown categorical value
- **THEN** the API SHALL return a validation error and the item SHALL remain in its prior state

#### Scenario: Unassigned user cannot claim
- **WHEN** the queue has assigned users and a non-assigned workspace member claims an item
- **THEN** the API SHALL return a permission error

#### Scenario: Skip without scoring
- **WHEN** a reviewer skips a claimed item
- **THEN** the item SHALL become `skipped` and no score records SHALL be written

### Requirement: Item detail with trace projection and machine-score prefill
`GET .../items/{item_id}` SHALL return the item state plus the review context: the conversation projected from the session event log within the trace's time window (user/assistant messages and tool calls, the same read-side projection the online scorer consumes), and for each queue metric the latest automated score for that trace as a read-only `suggestion` (with its `source`, `value`, and `reasoning`). The projection SHALL NOT modify the trace or events.

#### Scenario: Reviewer sees projected conversation and judge prefill
- **WHEN** an item's trace window contains 2 user/assistant exchanges and 1 tool call, and an online task scored the trace at 0.35 for `helpfulness`
- **THEN** the item detail returns the 4 messages and the tool call, and `suggestions` include `{metric_name: "helpfulness", value: 0.35, source: "llm_judge"}`

### Requirement: Human scores in the shared ledger
Submitted annotations SHALL be persisted in the target-typed score store with `source="human"`, `task_id=NULL`, `annotator_id` set to the submitting user, and `session_id`/`agent_id` denormalized from the target trace. Human rows SHALL coexist with automated rows (no automated row is modified or deleted by annotation). Resubmission by the same annotator for the same `(target_id, metric_name)` SHALL upsert that annotator's previous human row instead of creating a duplicate.

#### Scenario: Submission creates coexisting human row
- **WHEN** a reviewer submits an annotation for metric `helpfulness` on a trace that already has an automated score of 0.35
- **THEN** a human row with `value` from the submission, `source="human"`, and `task_id=NULL` SHALL exist alongside the automated row, and the automated row SHALL be unchanged

#### Scenario: Annotator resubmission is an upsert
- **WHEN** the same annotator submits a new value for the same trace and metric (via another queue)
- **THEN** their previous human row SHALL be updated to the new value and no second row SHALL be created

### Requirement: Override semantics
An annotation entry MAY reference `overrides_score_id` — the identifier of an existing automated score row (`source` != `human`) for the same `target_id` and `metric_name`. For an override entry, `reason_code` (non-empty, at most 50 characters) and `justification` SHALL be required; a mismatched target or metric reference SHALL be rejected. The referenced automated row SHALL remain unchanged; reconciliation (see the report dashboard capability) reads the human value in place of the overridden automated value. Multiple overrides of the same automated row SHALL be allowed and the latest by creation time SHALL win in reconciliation.

#### Scenario: Valid override calibrates a machine score
- **WHEN** a reviewer submits `{metric_name: "faithfulness", value: 0.9, overrides_score_id: S, reason_code: "judge_wrong_fact", justification: "the cited passage does support the claim"}` where S is an automated faithfulness score of 0.2 for the same trace
- **THEN** a human row with `overrides_score_id=S` SHALL be persisted and row S SHALL be unchanged

#### Scenario: Override without reason is rejected
- **WHEN** a submit carries `overrides_score_id` without `reason_code` or `justification`
- **THEN** the API SHALL return a validation error

#### Scenario: Cross-target override reference is rejected
- **WHEN** `overrides_score_id` references a score row of a different trace or a different metric
- **THEN** the API SHALL return a validation error

### Requirement: Dataset backflow from completed annotations
`POST /api/evaluation/annotation-queues/{queue_id}/push-dataset` with `{dataset_id | dataset_name, item_ids?}` SHALL materialize completed items (the given subset, or all completed items of the queue) into evaluation dataset items: `query` from the projected final user message, `generated_answer` from the projected final assistant message, `metadata_` recording `{trace_id, queue_id, queue_item_id, labels}` (the submitted human annotations), and `tags` including `human-annotation` and the queue name. The push SHALL be idempotent per dataset: a trace already materialized in the target dataset SHALL be skipped. Items without any submitted annotation SHALL NOT be pushed. The response SHALL report created and skipped counts.

#### Scenario: Push completed annotations into a dataset
- **WHEN** 2 completed items are pushed to dataset D
- **THEN** D SHALL contain 2 new items whose metadata records the source trace and human labels, and whose tags include `human-annotation`

#### Scenario: Duplicate push is idempotent
- **WHEN** the same queue is pushed to dataset D again with no new completed items
- **THEN** no new dataset items are created and the response reports 0 created

#### Scenario: Dataset name creates the dataset on first push
- **WHEN** `push-dataset` is called with `dataset_name: "human-golden-v1"` that does not exist
- **THEN** the dataset SHALL be created in the workspace and items materialized into it

### Requirement: Calibration analytics
The system SHALL expose `GET /api/evaluation/calibration` with optional filters `agent_id`, `task_id`, `metric_name`, and the standard time window. For each metric with machine-human paired samples (an automated row and a human row for the same `target_id` and `metric_name`), the response SHALL report: `pair_count`; for numeric metrics `agreement_rate` (fraction of pairs within an absolute tolerance of 0.1), `mae` (mean absolute error), and a 10-bucket machine-value × human-value heatmap; for categorical/boolean metrics the exact-match `agreement_rate` and Cohen's Kappa. Metrics without paired samples SHALL report zeroed stats; unpaired human-only and machine-only counts SHALL be reported per metric.

#### Scenario: Numeric calibration over paired samples
- **WHEN** metric `helpfulness` has 10 paired samples with mean absolute difference 0.05 and 9 pairs within 0.1
- **THEN** the response reports `pair_count: 10`, `agreement_rate: 0.9`, and `mae: 0.05`

#### Scenario: Categorical calibration uses Kappa
- **WHEN** metric `tone` (categorical) has 8 paired samples with observed agreement 0.75
- **THEN** the response reports the exact-match agreement rate and Cohen's Kappa for the metric

#### Scenario: Workspace isolation
- **WHEN** the calibration endpoint is called from workspace A
- **THEN** only workspace A's score rows are included in the statistics

### Requirement: Annotation view in ops center
The evaluation page SHALL include an Annotation view with: a queue list (name, instructions, per-state item counts, metric chips, assignees), a queue workbench for reviewing items (projected conversation and tool calls, machine-score suggestions, one input control per metric definition rendered by its data type, an override toggle exposing `reason_code` and `justification` when enabled, submit and skip actions, and next/previous item navigation), and an "add to annotation queue" action on the Online Quality session drill-down and low-score sample lists. The workbench SHALL present the suggestion values as pre-filled defaults the reviewer can accept or change.

#### Scenario: Reviewer completes an item in the workbench
- **WHEN** a reviewer opens a claimed item, keeps the prefilled `helpfulness` suggestion, overrides `faithfulness` from 0.2 to 0.9 with a reason, and submits
- **THEN** the submission succeeds, the item shows `completed`, and navigation moves to the next pending item

#### Scenario: Entry point from online quality
- **WHEN** the user selects a low-score sample in the Online Quality view and chooses "add to annotation queue"
- **THEN** the trace is enqueued in the selected queue and the UI confirms

### Requirement: Annotator identity and audit attribution
All queue and item mutations SHALL be attributed to the authenticated workspace user: item creation records `added_by`, claim records `claimed_by`, submission records the annotator on each human score row, and completion records `completed_by`. Cross-workspace access SHALL behave as if the resource does not exist.

#### Scenario: Submission is attributed
- **WHEN** user U submits an annotation
- **THEN** every human score row created by that submission records `annotator_id = U`
