## ADDED Requirements

### Requirement: Structured security audit event integration
The existing AuditLog system SHALL be extended to consume SecurityAuditEvent records from the structured security audit pipeline (9.14). Security audit events SHALL be queryable via a dedicated REST endpoint separate from the general audit log API.

#### Scenario: Security audit events queryable via dedicated endpoint
- **WHEN** a client requests `GET /api/security/audit?agent_id={id}`
- **THEN** the system returns SecurityAuditEvent records (not general AuditLog records)
- **AND** the response includes decision, reason, policy_version, and per-layer breakdown

#### Scenario: Security audit events available for SIEM export
- **WHEN** the SIEM export pipeline (8.7 SS5) is implemented
- **THEN** it SHALL consume SecurityAuditEvent records as its primary data source
- **AND** convert them to CEF/LEEF/JSON format for external SIEM systems
