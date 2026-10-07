## ADDED Requirements

### Requirement: Persistent run views survive successor attempts
The Runner SHALL return each Run's own status after restart and SHALL NOT expose a later attempt's wait token through an earlier Run.

#### Scenario: Waiting attempt is resumed and host restarts
- **WHEN** a successor Run succeeds and the host restarts
- **THEN** the original Run retains its recorded waiting state without a consumable wait token and the successor reports succeeded

### Requirement: Event delivery exposes complete pagination
The Runner SHALL propagate persistent event pagination and SHALL NOT label queued, waiting, or reconciliation-required Task states as terminal completion.

#### Scenario: Completed run has multiple event pages
- **WHEN** the first event page is full and more events exist
- **THEN** the formal page declares has_more and the compatible stream does not close before its last page

#### Scenario: Waiting task is queried
- **WHEN** the Task is waiting for approval
- **THEN** its event stream is not reported as terminal

### Requirement: Restart preserves admission and result receipts
The Runner SHALL return the original received_at and idempotency key on submission replay after restart and SHALL retain the original error and persisted artifact references.

#### Scenario: Completed submission is replayed after restart
- **WHEN** the same request is submitted again
- **THEN** the submit receipt is unchanged and no additional execution occurs

### Requirement: Repeated cancellation preserves pending effects
The Runner SHALL reuse a pending cancellation receipt for repeated running-attempt cancellation and SHALL reject cancellation of an already completed persistent Run without an internal error.

#### Scenario: Cancellation requested twice before taking effect
- **WHEN** the cooperative cancellation boundary has not taken effect
- **THEN** both requests refer to the same pending command and its receipt converges on the actual effect

#### Scenario: Host crashes after persisting cancelled
- **WHEN** cancellation produces a cancelled terminal fact
- **THEN** its applied receipt is committed in the same local transaction, without a later update requirement

#### Scenario: Cancel completed Run after restart
- **WHEN** no in-memory execution remains for a completed Run
- **THEN** the cancellation receipt is rejected and historical applied receipts remain unchanged
