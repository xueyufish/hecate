## MODIFIED Requirements

### Requirement: Audit security policy engine
The system SHALL implement a rule-based `FindingEngine` (renamed from `PolicyEngine`) that evaluates audit events against configurable `DetectionRule`s (renamed from `AuditSecurityPolicy`). The system SHALL include 3 built-in rules: `BulkDeleteRule` (renamed from `BulkDeleteProtectionPolicy`) which alerts when same user deletes 5+ resources in 1 minute, `OffHoursRule` (renamed from `OffHoursSensitiveOpsPolicy`) which alerts when sensitive operations occur outside configured business hours, and `UnusualIPRule` (renamed from `UnusualIPDetectionPolicy`) which alerts when login from IP not in user's recent history. Rule violations SHALL be persisted as `SecurityFinding` records (renamed from `PolicyViolation`) to the `security_findings` table instead of being discarded via `log.warning()`. The `FindingSeverity` enum (renamed from `PolicySeverity`) defines levels: LOW, MEDIUM, HIGH, CRITICAL.

#### Scenario: Bulk delete detected and persisted
- **WHEN** a user performs 5 or more delete operations within 1 minute
- **THEN** the FindingEngine creates a SecurityFinding with `rule_name="bulk_delete_rule"` and `severity="medium"`
- **AND** the finding is persisted to the `security_findings` table
- **AND** the finding is queryable via `GET /api/security/findings`

#### Scenario: Off-hours sensitive operation detected
- **WHEN** a workspace delete operation occurs at 2:00 AM on a Sunday
- **THEN** the FindingEngine creates a SecurityFinding with `rule_name="off_hours_rule"` and `severity="low"`
- **AND** the finding is persisted to the `security_findings` table

#### Scenario: Unusual IP detected
- **WHEN** a user performs an action from an IP address not in their known IP set
- **THEN** the FindingEngine creates a SecurityFinding with `rule_name="unusual_ip_rule"` and `severity="low"`
- **AND** the finding is persisted to the `security_findings` table

#### Scenario: FindingEngine failure does not block audit
- **WHEN** the FindingEngine encounters a database error during persistence
- **THEN** the error is logged and the audit pipeline continues processing
- **AND** no exception propagates to the AuditMiddleware

#### Scenario: Custom DetectionRule registration
- **WHEN** a user implements a custom `DetectionRule` subclass
- **THEN** the rule can be registered with `FindingEngine.register(rule)` and evaluated alongside built-in rules
