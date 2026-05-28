## ADDED Requirements

### Requirement: Sandbox pool pre-warming
The system SHALL pre-create sandbox containers for fast allocation.

#### Scenario: Pool has available sandbox
- **WHEN** a tool needs sandbox execution
- **THEN** the system allocates from pool (fast path)

#### Scenario: Pool exhausted
- **WHEN** no sandboxes available in pool
- **THEN** the system creates a new container (slow path)

### Requirement: Sandbox recycling
The system SHALL recycle sandboxes after use.

#### Scenario: Sandbox returned to pool
- **WHEN** tool execution completes
- **THEN** the sandbox is cleaned and returned to pool

#### Scenario: Sandbox destroyed after max uses
- **WHEN** a sandbox has been used N times
- **THEN** the system destroys it and creates a fresh one
