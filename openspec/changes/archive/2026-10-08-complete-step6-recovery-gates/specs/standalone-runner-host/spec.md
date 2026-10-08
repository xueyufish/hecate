## ADDED Requirements

### Requirement: Persistent commands are the only way to wake waiting tasks

Durable runner hosts SHALL wake waiting_input and waiting_approval tasks only through persistent command records. A wake command SHALL bind the original Task/Run, wait token, expected waiting kind, input digest, issuer, expiry, and command_id; the host SHALL atomically validate the binding, consume the token, merge input, create a successor attempt, and mark the command applied. Expired, mismatched, replayed, or duplicate commands SHALL be rejected or replay the prior receipt and MUST NOT dispatch tools.

#### Scenario: Mismatched wake command is rejected without tool dispatch

- **WHEN** a wake command targets the wrong Run, waiting kind, or token
- **THEN** the command is rejected, the wait token remains unchanged, and no tool or business API is called

#### Scenario: Valid wake survives host restart

- **WHEN** a task enters waiting_input, the host is terminated, and a valid wake command arrives after restart
- **THEN** the host consumes the stored wait token once, creates a successor Run, and executes that successor once

### Requirement: Native continuation preserves frozen action identity

Durable runner hosts SHALL resume interrupted attempts through the backend's native checkpoint when available and SHALL preserve frozen action ids, tool schema digests, model references, principal, deployment, and data-domain snapshots. Completed protected actions SHALL replay their stored result references; claimed or outcome_unknown protected actions SHALL enter reconciliation and MUST NOT be re-executed automatically. If native continuation is unavailable, the host SHALL use the existing ledger replay semantics and report that no native checkpoint was used.

#### Scenario: Completed action result is replayed during continuation

- **WHEN** a host resumes an interrupted attempt with a completed protected action
- **THEN** the business API is not called again and the stored result reference is returned to the runtime

#### Scenario: Unknown write blocks automatic continuation

- **WHEN** the checkpoint references a protected action whose external outcome is unknown
- **THEN** the host marks the task reconciliation_required and does not continue by reissuing that action

### Requirement: Evidence gate failure stops new protected actions

Before dispatching a protected action, the runner SHALL verify that local evidence and action ledger writes are available and within configured local limits. If evidence is not writable or the local buffer is over limit after retention cleanup, the runner SHALL reject new protected actions fail-closed while allowing readonly actions to proceed according to existing policy. The rejection SHALL be queryable locally and SHALL record whether the cause was unwritable evidence, capacity, or lease failure.

#### Scenario: Evidence storage unavailable rejects protected action

- **WHEN** evidence storage cannot accept the probe or rejection record
- **THEN** a new protected action is not dispatched, readonly actions remain eligible, and the protected business API call count is zero
