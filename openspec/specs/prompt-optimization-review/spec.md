## Purpose

Lets users review and publish the output of prompt self-optimization runs: gate-passing candidates are presented with full evidence (diff, metric deltas, failure details, reflection summary, cost), passed through content safety scanning, and only become live prompt versions after explicit human approval — never auto-published.

## Requirements

### Requirement: Candidate evidence report
The system SHALL expose, for each candidate in a run's review pool, an evidence report containing: a line-level diff between the candidate template and the base template, per-metric scores with deltas against baseline, the pass/fail gate report, per-item failure details from validation rollouts (query, expected, generated, evaluator reasoning), the reflection summary that motivated the mutation, lineage (parent candidate and round number), and the candidate's rollout and LLM cost.

Candidate reads and decisions SHALL sit behind the evaluation access boundary with the run binding: every identity needs an active workspace, candidate visibility requires the candidate AND its parent run to be live in the workspace (a candidate whose workspace matches but whose parent run was deleted or moved is invisible, and listings never mix such candidates in), non-system callers need the viewer floor to read and the editor floor to decide, and invisible chains share one 404 that leaks neither the reason nor any evidence.

#### Scenario: Evidence report contents
- **WHEN** a reviewer opens a `pending_review` candidate
- **THEN** the response includes the template diff, per-metric deltas, gate report, per-item failure details, reflection summary, lineage, and cost

#### Scenario: Gate-rejected candidates are visible but not reviewable
- **WHEN** a candidate was rejected by the acceptance gate
- **THEN** it appears in the run's candidate list with its gate report, and approve/reject actions are not available for it

#### Scenario: Parent-run consistency governs visibility
- **WHEN** a candidate's parent run is missing, soft-deleted, or belongs to another workspace
- **THEN** the candidate detail and both decision endpoints return 404 without evidence content, and the run's candidate listing never includes it

### Requirement: Content safety scan before review
Every gate-passing candidate SHALL pass the platform's content safety scanning (content-scanning/DLP pipeline) before entering the review pool. A candidate blocked by the scan SHALL be marked `scan_blocked` with the finding reference, SHALL NOT be approvable, and the block SHALL be visible on the run's candidate list.

#### Scenario: Clean candidate enters review pool
- **WHEN** a gate-passing candidate completes scanning without findings
- **THEN** it is marked `pending_review` and is approvable

#### Scenario: Unsafe candidate blocked
- **WHEN** scanning flags a candidate's template content
- **THEN** the candidate is marked `scan_blocked` with the finding recorded and approve is rejected for it

### Requirement: Human approval publishes a new prompt version
Approving a candidate SHALL create a new immutable version of the target prompt with the candidate template. The new version SHALL carry: an auto-generated commit message referencing the optimization run and the metric deltas, and provenance metadata identifying the source (optimization run id, candidate id, baseline and candidate scores). The new version SHALL NOT receive any deployment label automatically; labels are managed by the existing label flow (protected labels keep their existing role rules). Publishing SHALL follow the prompt's normal version numbering.

The service SHALL re-verify the candidate→run→prompt chain with fresh reads inside the write transaction: a stale API precheck or a post-precheck rebinding SHALL NOT publish to another object, and approval SHALL additionally require the target prompt to be live in the workspace. Reviewer attribution SHALL come from the server-resolved identity.

The API SHALL capture the visible `(run_id, prompt_id)` binding as immutable values before the decision. Both decision methods SHALL compare their fresh binding against that admitted binding, including when both targets belong to the same workspace. Resource visibility and binding checks SHALL precede state-conflict errors. The write transaction SHALL protect the candidate, parent run, and publication target from concurrent rebinding; on SQLite, where row-locking reads are ignored, a conditional unchanged-binding claim SHALL guard the first write. Such a claim SHALL NOT alter decision fields or timestamps. Publication SHALL refresh the prompt's current version before allocating the new version number.

#### Scenario: Approval creates a provenance-carrying version
- **WHEN** a reviewer approves a `pending_review` candidate
- **THEN** a new prompt version is created with the candidate template, an auto-generated commit message naming the run and metric deltas, and provenance metadata (run id, candidate id, scores), with no labels applied

#### Scenario: Approved version behaves like any prompt version
- **WHEN** a candidate is published
- **THEN** the new version is diffable, analyzable, and label-manageable exactly like manually created versions

#### Scenario: Binding recheck blocks a stale publication
- **WHEN** the parent run is deleted or moved to another workspace after the API visibility precheck, or the candidate/run is rebound to a different run/prompt in the same workspace before the write
- **THEN** approval returns 404, no prompt version is created, the candidate stays undecided, and the prompt's current version is unchanged

### Requirement: Rejection with reason
A reviewer SHALL be able to reject a `pending_review` candidate; a rejection reason is required and persisted with the reviewer identity and timestamp. Rejecting the last undecided candidate of a run SHALL move the run to `concluded`.

Rejection SHALL re-verify the candidate→run binding with fresh reads before any mutation: an invisible or inconsistent chain changes nothing — no candidate status change, no reviewer recorded, no run convergence.

#### Scenario: Rejection persisted
- **WHEN** a reviewer rejects a candidate with a reason
- **THEN** the candidate status becomes `rejected` with the reason, reviewer, and timestamp recorded

#### Scenario: Run concludes after last decision
- **WHEN** every candidate in the review pool has a final decision (published, rejected, or scan_blocked)
- **THEN** the run status transitions to `concluded`

#### Scenario: Binding recheck blocks a stale rejection
- **WHEN** the candidate's run is deleted or moved after the API precheck
- **THEN** rejection returns 404 and the candidate, run, and prompt remain exactly as they were

### Requirement: No auto-publish and no auto-delete
The system SHALL NOT publish any candidate without an explicit human approval action, and SHALL NOT delete candidates, evidence, or run records as part of the workflow. Rejected and scan-blocked candidates remain queryable for the run's retention lifetime.

#### Scenario: No candidate ships silently
- **WHEN** a run reaches `awaiting_review` with gate-passing candidates
- **THEN** no prompt version is created until a reviewer approves at least one candidate

#### Scenario: Rejected candidates persist
- **WHEN** a run concludes
- **THEN** rejected and scan-blocked candidates with their evidence remain queryable

### Requirement: Rollback via existing prompt versioning
A published optimization candidate SHALL be rollback-safe using the platform's existing prompt version semantics only (version switching and label management). The optimization workflow SHALL NOT introduce a separate rollback mechanism.

#### Scenario: Rollback after publish
- **WHEN** a reviewer re-points the prompt to the pre-optimization version (or moves labels back)
- **THEN** the prompt serves the previous version exactly as with any manual version change, and no optimization-specific rollback API is involved
