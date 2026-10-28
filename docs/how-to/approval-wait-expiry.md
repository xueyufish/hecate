# Approval wait expiry

A task parked in `waiting_approval` waits for one decision, and the wait carries its own deadline: the single-use token's `wait_expires_at` plus the pending approval binding's authority expiry, minted together when the task parked (60 minutes by default, `DEFAULT_PARK_TTL_MINUTES`). This page describes what happens when nobody answers in time.

## Who converges an expired wait

The durable dispatch worker runs an approval-wait expirer on every reconcile cycle (the in-process worker wires it automatically on the postgres binding; a dedicated worker process opts in with `--wait-reconciler hecate.execution.approval_wait_expiration:standalone_expirer`). The dedicated factory receives the worker's store and rejects a database mismatch with the configured platform session factory. Deployment profiles that run **without** a durable worker have no automatic convergence — expired wakes are rejected and the task stays parked until an explicit operational resolution or an expiry worker is enabled.

A pass reads a bounded SQL keyset page and advances its scan cursor even when nothing expires, so early unexpired records cannot starve later tasks. One broken record never blocks the others. Platform issuance alone does not prove ownership: the task/run/workspace mapping, platform control owner, local builtin deployment and absence of managed host delivery must agree. The authority must also match the parked action and principal. Host-owned waits, `waiting_input`, consumed tokens and waits without a declared deadline are untouched.

## What expiry does

- The task fails with the stop category `approval_wait_expired`. The state transaction checks the revision, exact current wait and effective deadline after locking the task row. Resume uses the same commit-time deadline guard, so crossing the deadline after the initial gate cannot produce an applied wake.
- The wait token is consumed; the approval reference, both deadlines and every recorded action fact are preserved. The approval binding itself is never rewritten and never extended.
- Nothing re-executes: no model call, no tool dispatch, no action redo. Expiry terminates the wait; it is not an outcome of the work.
- A rejection that somehow never converged keeps its real `approval_rejected` verdict — expiry is never rewritten into (or out of) a human decision. An approval that was granted but never consumed still converges as expired: a lapsed authority cannot authorize anything.
- The terminal fact rides the transactional outbox: the run projection, the `run_terminal` event and the workflow-parent callback are recovered by the relay if the direct writes are lost. A workflow child that expires converges its waiting parent through the verified callback path under the parent's declared `on_failure` policy.

## What expiry does not do

- It cannot extend either deadline, and it does not guess: a wait whose authority record is missing, or whose trusted time cannot be re-anchored against the database, is parked as `reconciliation_required` with an explicit reason instead of being expired on assumption.
- Late traffic changes nothing after convergence: a late approval decision, an expired token, a stale revision or a replayed command is rejected without reopening the task.
- Queries stay passive — reading a task never expires its wait (no lazy expiry); only the worker's reconciliation pass does.

## Verification pointers

Deterministic coverage lives in `tests/test_execution/test_approval_wait_expiration.py` (park → expiry → terminal facts, commit-time wake rejection, repeated/concurrent scans, independent-process recovery, clock trust, ownership and binding negatives) and `tests/test_execution/test_workflow_approval_combo.py` (both parent failure policies and callback-loss recovery). These fixtures use SQLite; setting `DURABLE_TEST_POSTGRES_URL` does not turn them into PostgreSQL tests. PostgreSQL concurrency certification remains pending a dedicated run.
