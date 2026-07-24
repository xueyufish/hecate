## ADDED Requirements

### Requirement: SecurityAuditEvent data model
The system SHALL provide a `SecurityAuditModel` ORM table that stores structured security audit events. Each event SHALL capture: agent_id, workspace_id, session_id (nullable), tool_name, arguments_hash (SHA-256), decision, reason, policy_version, on_behalf_of_user (nullable), timestamp, and per-layer decision breakdown.

#### Scenario: Audit event created on policy evaluation
- **WHEN** `ToolPolicyPipeline.evaluate_execution()` returns a decision for tool `bash`
- **THEN** a `SecurityAuditModel` row is created with the tool name, final decision, reason, and per-layer results

#### Scenario: Arguments stored as hash not raw
- **WHEN** a tool call with arguments `{"command": "rm -rf /tmp/data"}` is evaluated
- **THEN** the audit event stores `arguments_hash` as SHA-256 of the arguments
- **AND** raw arguments are NOT stored in the audit table

#### Scenario: Policy version recorded
- **WHEN** a policy evaluation occurs
- **THEN** the audit event records `policy_version` as a hash of the effective policy configuration at evaluation time

### Requirement: Async batch write for audit events
The system SHALL buffer security audit events in memory and flush to the database in batches (every 50 events or 5 seconds, whichever comes first).

#### Scenario: Events buffered until threshold
- **WHEN** 30 audit events are generated within 5 seconds
- **THEN** events remain in the in-memory buffer (not yet written to database)

#### Scenario: Flush on event count threshold
- **WHEN** the 50th event is added to the buffer
- **THEN** all 50 events are flushed to the database in a single batch write
- **AND** the buffer is cleared

#### Scenario: Flush on time threshold
- **WHEN** 5 seconds pass since the last flush and the buffer has 10 events
- **THEN** all 10 events are flushed to the database
- **AND** the buffer is cleared

#### Scenario: Flush on graceful shutdown
- **WHEN** the application receives a shutdown signal
- **THEN** all buffered events are flushed before shutdown completes

### Requirement: Audit event emission from policy evaluations
The system SHALL automatically emit `SecurityAuditEvent` from three emission points: `ToolPolicyPipeline.evaluate_visibility()`, `ToolPolicyPipeline.evaluate_execution()`, and `ToolAccessPolicy.evaluate()`.

#### Scenario: Visibility evaluation emits per-tool event
- **WHEN** `evaluate_visibility()` filters out a tool (HIDE or DENY)
- **THEN** an audit event is emitted with the tool name, layer that caused hiding, and decision

#### Scenario: Execution evaluation emits final decision event
- **WHEN** `evaluate_execution()` returns a final decision with per-layer results
- **THEN** an audit event is emitted with the final decision, reason, and all layer results

#### Scenario: ToolAccessPolicy emits access decision event
- **WHEN** `ToolAccessPolicy.evaluate()` returns `REQUIRE_APPROVAL`
- **THEN** an audit event is emitted with the access decision, matched rule, and risk level

### Requirement: REST API for audit event query
The system SHALL expose a REST API endpoint for querying security audit events with filtering.

#### Scenario: Query by agent
- **WHEN** a client requests `GET /api/security/audit?agent_id={agent_id}`
- **THEN** the system returns all audit events for that agent within the default time window

#### Scenario: Query by decision
- **WHEN** a client requests `GET /api/security/audit?decision=DENY`
- **THEN** the system returns only audit events where the decision was DENY

#### Scenario: Query by time range
- **WHEN** a client requests `GET /api/security/audit?start=2026-07-20T00:00:00&end=2026-07-24T00:00:00`
- **THEN** the system returns only events within the specified time range

#### Scenario: Pagination
- **WHEN** a client requests `GET /api/security/audit?limit=50&offset=100`
- **THEN** the system returns 50 events starting from offset 100

### Requirement: Configurable retention with auto-cleanup
The system SHALL automatically delete audit events older than the configured retention period.

#### Scenario: Default retention is 30 days
- **WHEN** `AGENT_ENV_AUDIT_RETENTION_DAYS` is not set
- **THEN** events older than 30 days are eligible for cleanup

#### Scenario: Cleanup task runs periodically
- **WHEN** the cleanup task runs (daily)
- **THEN** all events with `timestamp < now() - retention_days` are deleted

#### Scenario: Audit disabled stops event emission
- **WHEN** `AGENT_ENV_AUDIT_ENABLED=false`
- **THEN** no `SecurityAuditEvent` rows are created
- **AND** policy evaluations proceed without audit overhead

### Requirement: Audit pipeline works on both environments
The structured audit pipeline SHALL function on both LocalEnvironment and DockerEnvironment.

#### Scenario: LocalEnvironment emits audit events
- **WHEN** `AGENT_ENV_BACKEND=local` and `AGENT_ENV_AUDIT_ENABLED=true`
- **THEN** policy evaluations emit audit events normally
- **AND** events are queryable via REST API

#### Scenario: DockerEnvironment emits audit events
- **WHEN** `AGENT_ENV_BACKEND=docker` and `AGENT_ENV_AUDIT_ENABLED=true`
- **THEN** policy evaluations emit audit events normally
- **AND** events are queryable via REST API
