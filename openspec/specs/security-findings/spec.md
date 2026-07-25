# security-findings Specification

## Purpose

Define the SecurityFinding data model, persistence layer, retention policy, and REST API for querying anomaly detection findings produced by the FindingEngine.

## Requirements

### Requirement: SecurityFindingModel data model

The system SHALL provide a `SecurityFindingModel` ORM table (`security_findings`) that stores anomaly detection findings produced by the FindingEngine. Each finding SHALL capture: org_id, workspace_id, user_id (nullable), rule_name, severity, message, source_event (JSON of the triggering event), metadata (JSON), and created_at timestamp.

#### Scenario: Finding persisted on rule match

- **WHEN** the FindingEngine detects a bulk-delete violation
- **THEN** a `SecurityFindingModel` row is created with `rule_name="bulk_delete_rule"`, `severity="medium"`, the triggering event in `source_event`, and violation context in `metadata`

#### Scenario: Finding severity indexed for filtering

- **WHEN** a finding is created with `severity="critical"`
- **THEN** the severity field is indexed to support efficient `WHERE severity >= 'high'` queries

### Requirement: FindingEngine replaces PolicyEngine

The system SHALL rename `PolicyEngine` to `FindingEngine`, `AuditSecurityPolicy` ABC to `DetectionRule` ABC, `PolicyViolation` to `SecurityFinding`, `PolicyContext` to `DetectionContext`, and `PolicySeverity` to `FindingSeverity`. All existing behavior SHALL remain identical except findings are persisted instead of discarded.

#### Scenario: FindingEngine evaluates events

- **WHEN** the FindingEngine receives an audit event
- **THEN** all registered DetectionRules evaluate the event against DetectionContext
- **AND** any resulting SecurityFindings are persisted to the database

#### Scenario: Built-in rules renamed

- **WHEN** the system initializes with default rules
- **THEN** `BulkDeleteProtectionPolicy` is renamed to `BulkDeleteRule`, `OffHoursSensitiveOpsPolicy` to `OffHoursRule`, and `UnusualIPDetectionPolicy` to `UnusualIPRule`

### Requirement: Finding persistence replaces log.warning

The system SHALL persist all SecurityFindings to the `security_findings` table instead of calling `log.warning()` and discarding them. Logging SHALL continue at DEBUG level for operational visibility, but persistence is the primary record.

#### Scenario: Finding no longer lost

- **WHEN** the OffHoursRule detects a weekend sensitive operation
- **THEN** a SecurityFindingModel row is created in the database
- **AND** the finding is queryable via the REST API

#### Scenario: FindingEngine failure does not block audit pipeline

- **WHEN** the database is unavailable during finding persistence
- **THEN** the FindingEngine logs an error and continues processing subsequent events
- **AND** no exception propagates to the caller

### Requirement: REST API for finding query

The system SHALL expose `GET /api/security/findings` with filtering by org_id, workspace_id, user_id, rule_name, severity, and time range.

#### Scenario: Query by severity

- **WHEN** a client requests `GET /api/security/findings?severity=high`
- **THEN** the system returns only findings with severity HIGH or CRITICAL

#### Scenario: Query by rule name

- **WHEN** a client requests `GET /api/security/findings?rule_name=bulk_delete_rule`
- **THEN** the system returns only findings from the bulk delete detection rule

#### Scenario: Query by time range with pagination

- **WHEN** a client requests `GET /api/security/findings?start=...&end=...&limit=50&offset=0`
- **THEN** the system returns up to 50 findings within the time range, ordered by created_at descending

### Requirement: Finding retention with auto-cleanup

The system SHALL automatically delete findings older than the configured retention period (`SECURITY_FINDING_RETENTION_DAYS`, default 90 days).

#### Scenario: Default retention is 90 days

- **WHEN** `SECURITY_FINDING_RETENTION_DAYS` is not set
- **THEN** findings older than 90 days are eligible for cleanup

#### Scenario: Cleanup task runs daily

- **WHEN** the cleanup task runs
- **THEN** all findings with `created_at < now() - retention_days` are deleted
