# Decide human approval waits on platform tasks

A platform task parked in `waiting_approval` carries a PENDING authority
binding: the exact parked action (tool, arguments digest, target
resources, task scope) plus an expiry minted at park time. Only a
workspace ADMIN may record the human decision — approve or reject — and
the task's initiator can never decide its own approval. This guide covers
the operator-visible loop and the recovery semantics.

## The closed loop

1. **Park**: a governed tool whose policy requires approval raises the
   durable wait signal; the dispatcher persists the PENDING binding and
   parks the task (`waiting_approval`, single-use `wait_token`, expiry =
   the binding's authority expiry).
2. **Decide**: on the task detail page (审批决定 panel), an ADMIN enters
   an optional reason and approves or rejects. The panel freezes one
   decision operation — a stable `operation_id` plus the approval
   reference, the page-read task revision, and the wait token. The server
   derives the decider identity from the authenticated context; request
   bodies carry no authority.
3. **Facts**: the decision record states what was decided, by whom, and
   when. The resume receipt states whether the platform accepted the
   recorded decision as a wake. `applied` on the resume is neither the
   protected action executing nor the task succeeding — refresh the task
   and run records for those facts.
4. **Resume/convergence**: an approved decision re-drives the original
   execution through the existing wake gate and one-time binding
   consumption (revocation, expiry, parameter drift and action identity
   are re-checked at dispatch; already-completed actions never re-run).
   A rejected decision fails the task through the existing rejection
   path (`approval_rejected`) and converges any workflow parent the same
   way a callback rejection would.

## Who may decide, and what is read-only

- The POST gate requires the server-side ADMIN role in the task's
  active workspace. EDITOR `resume` permission does not manufacture an
  approval fact, and VIEWER requests are forbidden outright.
- The task initiator (the persisted `user_id` the park identity was
  derived from) and the proposal Agent are refused with `SELF_APPROVAL`.
  Explicitly system-initiated tasks have no human initiator; missing or
  conflicting responsibility records do not count as system initiation.
- A task whose initiating identity is corrupt or unparseable is
  read-only (`INITIATOR_UNPARSEABLE`): responsibility cannot be
  established, so nobody decides.
- Approval references carry no workspace of their own: the service
  verifies the reference against the current task's live wait record,
  so a foreign `approval_ref` is always a mismatch.
- Only platform-local durable waits with complete action identities are
  supported. Managed remote hosts and unknown historical wait shapes are
  read-only. The server validates the Task/Run owner, deployment, token,
  required task revision, original deadlines and complete authority binding
  before recording a new judgment.
- Consumed or expired waits, terminal tasks, revoked authority, and missing
  responsibility records render read-only with their reasons. The query's
  `can_decide` is a display hint; every POST repeats authorization.

## Operation identity, races and recovery

- One decision operation = one `operation_id`. The same id with the
  same body replays the recorded facts; the same id with a changed
  decision, reason, identity, task, workspace, approval reference, token or
  task revision conflicts (`OPERATION_CONFLICT`). An old operation still
  replays its original facts after the task ends or enters another wait;
  it never decides the new approval. A new wait uses a new operation id.
- Concurrent opposite decisions arbitrate on the authority's
  compare-and-set: exactly one transition wins, the loser receives
  `ALREADY_DECIDED`, and the binding reflects only the winner.
- Authority judgment and the operation/audit row commit in one SQL
  transaction. Failure to persist the operation rolls back the judgment;
  another operation cannot adopt a pre-existing decision or change its reason.
- The decision and its resume are separate durable phases. A crash
  between them leaves the operation row without a resume receipt; the
  worker's named recovery consumer re-issues the deterministic resume
  from the stored row (the command id is derived from the operation
  id through a bounded hash), and receipt replay plus the single-use token
  make that safe. Recovery uses the bound worker and isolates each operation's
  transaction. Failed records rotate behind untouched pending work; a failed
  expiry pass does not suppress the resume recovery pass, or conversely.
  Retrying the POST completes the same phase idempotently. The server owns
  the `approval-resume-` command-id namespace. Resume transports only the
  authority reference, never a synthetic business-input decision.
- The original expiry is never extended: resuming, retrying or a worker
  restart is not re-authorization. An expired or revoked binding fails
  the protected action closed at dispatch (`approval_expired` /
  `approval_revoked`), and a cancelled task rejects the resume with a
  receipt — the recorded decision stays on the audit trail.
- Demoting the deciding ADMIN before a retry blocks the retry at the
  role gate (403) without disturbing the recorded facts.

## Operator checklist

- Task stuck in `waiting_approval` past its expiry: the expiration
  reconciler converges it on the worker cycle; the page shows the
  original expiry hint; server validation determines validity.
- Resume receipt `rejected` after an approval: the wait token expired,
  the task was cancelled, or the revision moved — the decision remains
  recorded; re-drive with a fresh request if still needed.
- The panel is read-only: check the reason line (consumed, approval
  wait already decided, corrupt initiator) before escalating.


## Deployment and verification boundaries

Upgrade the schema before enabling the decision endpoint or its recovery
consumer. The approval operation migration supplies database timestamp defaults
and a pending-recovery index; downgrading refuses to drop any operation/audit
rows, including soft-deleted records. Existing approvals remain the authority.

Automatic recovery is composed into the platform application's enabled durable
worker. A deployment with that worker disabled must run an equivalent configured
consumer, or explicitly retry the same POST; a generic standalone worker does
not acquire this platform callback implicitly. Observation endpoints never run
recovery or re-anchor approval clocks.

Deterministic coverage includes a real governed Controller path: two identical
parameter calls require distinct approvals, fresh workers replay old actions,
each approved binding is consumed by its exact invocation, and rejection executes
no protected call. Migration DDL and retained-audit tests complement ORM-backed
tests. PostgreSQL concurrency, full migration-chain checks and real-provider
quality remain separate verification scopes; skipped checks do not certify them.
