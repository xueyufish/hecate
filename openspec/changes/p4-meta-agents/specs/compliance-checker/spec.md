## ADDED Requirements

### Requirement: Scan for architecture violations
The system SHALL scan codebase for architecture violations (code style, security config, dependency versions).

#### Scenario: Check code style compliance
- **WHEN** compliance checker runs
- **THEN** it checks for ruff violations in src/

### Requirement: Generate compliance report
The system SHALL generate a compliance report with violations and recommendations.

#### Scenario: Compliance report
- **WHEN** scan completes
- **THEN** the system generates a report with violation counts and fix recommendations
