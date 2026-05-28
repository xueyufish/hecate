## ADDED Requirements

### Requirement: Optimistic locking for channel updates
The system SHALL use optimistic locking to prevent conflicting channel updates.

#### Scenario: No conflict
- **WHEN** two agents update different channels
- **THEN** both updates succeed

#### Scenario: Conflict detected
- **WHEN** two agents update the same channel simultaneously
- **THEN** the system detects conflict and applies resolution strategy

### Requirement: Merge strategy for compatible updates
The system SHALL merge compatible updates (lists, maps) without conflict.

#### Scenario: List merge
- **WHEN** two agents append to the same topic channel
- **THEN** the system merges both appends

#### Scenario: Last-write-wins for simple values
- **WHEN** two agents update the same last_value channel
- **THEN** the system uses last-write-wins strategy

### Requirement: Human approval for critical conflicts
The system SHALL request human approval for critical resource conflicts.

#### Scenario: Critical conflict
- **WHEN** two agents conflict on a critical resource
- **THEN** the system pauses and requests human approval via Temporal Signal
