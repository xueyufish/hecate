## ADDED Requirements

### Requirement: Scan for expired resources
The system SHALL scan for expired sessions, orphaned checkpoints, and unused tools.

#### Scenario: Find expired sessions
- **WHEN** garbage collector runs
- **THEN** it identifies sessions older than retention period

### Requirement: Generate cleanup report
The system SHALL generate a report of resources to clean up.

#### Scenario: Cleanup report
- **WHEN** scan completes
- **THEN** the system generates a report with resource counts and estimated storage savings
