## ADDED Requirements

### Requirement: Fault matrix distinguishes completed, unknown, and late external writes

Durable execution SHALL preserve separate outcomes for protected actions whose business write completed, whose outcome is unknown, and whose result arrives late. Completed writes SHALL replay the original result reference without another business call; unknown writes SHALL remain reconciliation_required until an explicit reconciliation action supplies the outcome; late results SHALL be accepted only if they match the original frozen action id and ownership token, and MUST NOT reopen terminal projections.

#### Scenario: Business write completed but receipt upload was lost

- **WHEN** the business API write succeeded, the local result was persisted, and the platform receipt upload failed
- **THEN** restart or reconnect uploads the persisted result reference without calling the business API again

#### Scenario: Claimed write outcome unknown after process kill

- **WHEN** the process is killed after claiming a protected action and before persisting the business outcome
- **THEN** recovery marks the action and Task reconciliation_required and does not reissue the business write

#### Scenario: Late result cannot rollback terminal state

- **WHEN** a late external result arrives after the Run projection is already terminal
- **THEN** the result is recorded for reconciliation if it matches the frozen action, but the terminal projection is not rolled back or reopened

### Requirement: PostgreSQL acceptance covers host and platform crash recovery

The Step6 durable acceptance suite SHALL run against PostgreSQL storage for the full host fault matrix: accepted-before-execution, running process kill, completed write with lost receipt, unknown write, duplicate commands, late terminal events, platform restart, host reconnect, and multi-Run replay. SQLite tests MAY remain fast coverage but MUST NOT be the only evidence for Step6 completion.

#### Scenario: PostgreSQL host crash keeps idempotency and ledger decisions

- **WHEN** a PostgreSQL-backed host is terminated during a managed run and restarted
- **THEN** the same Task/Run and command receipts are recovered, completed actions are not repeated, and unknown actions remain pending reconciliation
