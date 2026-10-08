## ADDED Requirements

### Requirement: Managed commands produce effect receipts before platform projection changes

The platform SHALL persist managed control commands before delivery and SHALL treat delivery success as transport acknowledgement only. The host SHALL persist each received command in a local inbox keyed by command_id and target Task/Run, return the existing receipt for duplicate commands, and upload an effect receipt only after the command is applied, rejected, or expired. Platform projections SHALL change execution state only from host effect receipts or execution events, not from command delivery responses.

#### Scenario: Duplicate managed command returns the original receipt

- **WHEN** the same command_id is delivered twice to the managed host
- **THEN** the host returns the original command receipt and does not apply the command twice

#### Scenario: Delivery success does not mark the command applied

- **WHEN** the platform successfully delivers a cancel or resume command but the host has not yet applied it
- **THEN** the platform command remains requested or acknowledged, and the Task/Run projection is unchanged

### Requirement: Managed wake creates a platform-visible successor attempt

When a managed waiting task is legally woken by a technical token, the host SHALL consume the wait token once, merge the accepted input, create a successor local Run/attempt, and upload the successor association to the platform before successor execution events are folded into the platform projection. The original waiting Run SHALL remain queryable as waiting or terminal according to its own facts and MUST NOT inherit the successor state.

#### Scenario: Wake links successor attempt before execution projection

- **WHEN** a managed waiting task is woken and requeued
- **THEN** the platform records the successor Run association before it accepts running or terminal events from that successor

#### Scenario: Original waiting Run remains scoped to its facts

- **WHEN** the successor Run completes after a wake
- **THEN** queries for the original Run do not borrow the successor terminal state or wait token

### Requirement: Real platform HTTP acceptance closes the managed delivery gate

The managed delivery gate SHALL be closed only by an installed Runner process communicating with the platform over the public HTTP control-plane APIs and a real network transport. The acceptance test SHALL cover lost accept responses, resubmission, serial execution, status projection, host restart, reconnect, and multi-Run event replay. ASGI-only or in-process component tests MAY support development but MUST NOT mark this gate complete.

#### Scenario: Lost accept response does not create a second execution

- **WHEN** the platform loses the host's accept response and retries the same delivery over HTTP
- **THEN** the host returns the original accepted Task/Run, does not create a second execution, and later projects exactly one terminal result

#### Scenario: Reconnect replays each Run independently

- **WHEN** multiple managed Runs generate events while disconnected
- **THEN** reconnect uploads every Run's pending events without advancing one Run's cursor past another Run's events
