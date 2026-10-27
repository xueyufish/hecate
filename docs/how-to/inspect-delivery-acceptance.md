# Inspect a run's delivery and acceptance record

The Task detail page (`/ops-center/tasks/{task_id}`) shows the recorded delivery and acceptance facts for each Run (execution attempt) of a task. Everything on this surface is a recorded fact: reading it never re-runs a check, never calls a model, never dispatches a tool, and never rewrites a stored verdict.

## Where the facts come from

- The acceptance record is produced once, by the shared versioned evaluation (`delivery-acceptance/1`) both hosts use, and committed in the same transaction as the run's durable terminal state.
- The run projection stores a copy of that record. Two writers maintain it — the dispatcher's direct projection at terminal commit, and the outbox relay's recovery projection (the same path that recovers when a crash loses the direct write). Both write the same fields, and neither may erase an already-recorded verdict of the run.
- Task and Run queries (`GET /api/tasks/{task_id}`, `GET /api/runs/{run_id}`) return the record as a whitelisted `delivery_acceptance` view per run: status, standard version and digest, content digest, checker version, per-check verdicts, the final decision's identity and digest, and the responsible evaluator identity. The record's internal namespace references stay server-side.
- Reads validate the retained record's structure, digest and checker version, and bind its Task/Run namespace and any recorded workspace to the requested Run. A checksum alone does not prove ownership. Nested metadata uses explicit field lists; check failures and acceptance-related errors use generic explanations because raw schema diagnostics can include full output or standard parameters.
- Access follows the existing observation boundary: an active workspace, then resource visibility, then the VIEWER floor. Missing, foreign-workspace and soft-deleted tasks and runs are indistinguishable to an unauthorized caller.

## Reading the status column

| Status | Meaning |
| --- | --- |
| `passed` | Every declared required check passed, under the recorded standard version and digest |
| `acceptance_failed` | At least one declared check failed; the delivered content stays visible and confirmed actions are not redone |
| `acceptance_unverified` | A required check could not be executed (for example the checker was unavailable); this can never grant success |
| `not_declared` | The run delivered without any declared acceptance standard — success here is delivery only, never a business-goal acceptance |
| 无验收记录 | This run has no acceptance record at all (an execution that predates the surface, or one that never evaluated a delivery). This is not a verdict |
| `uninterpretable` | A stored record is malformed, fails integrity validation, belongs to another execution or workspace, or uses a checker version this build does not ship. It never displays as a pass |

Acceptance is separate from execution: a run can fail execution and still show a delivery preview, and a `passed` verdict only means the recorded checks passed when they were recorded — it is not a claim that the business goal is fully met.

## Preview truncation

The delivery preview is bounded and explicitly marked when truncated, including after backfill or outbox recovery. The full-content digest (`content_sha256`) is the recorded integrity reference — the preview must never be used to re-verify it. Reading does not retrieve the complete output through a failed check's diagnostic message.

## What this surface does not do

- It does not accept goal or standard revisions, and it does not re-open or re-evaluate a completed acceptance. Those belong to the acceptance-declaration flow at submission time.
- It does not show the task's latest result on a historical run: each attempt keeps its own recorded verdict even after a later attempt of the same task.
- Historical runs recorded before this surface existed show 无验收记录 rather than a guessed `not_declared`.
