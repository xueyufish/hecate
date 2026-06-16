# audit-logs Specification

## Purpose
TBD - created by archiving change p2-backend-features. Update Purpose after archive.
## Requirements
### Requirement: AuditLog model with monthly partitioning
The system SHALL persist audit log records in a PostgreSQL table partitioned by month on `created_at`. Each record SHALL contain: `id` (UUID PK), `org_id` (UUID, NOT NULL), `workspace_id` (UUID, nullable), `user_id` (UUID, NOT NULL), `action` (VARCHAR(100), NOT NULL), `resource_type` (VARCHAR(50)), `resource_id` (UUID, nullable), `request_method` (VARCHAR(10)), `request_path` (VARCHAR(500)), `response_status` (INTEGER), `ip_address` (VARCHAR(255)), `user_agent` (VARCHAR(500), nullable), `success` (BOOLEAN, NOT NULL), `error_code` (VARCHAR(100), nullable), `error_message` (TEXT, nullable), `metadata` (JSONB, default '{}'), `created_at` (TIMESTAMPTZ, NOT NULL).

#### Scenario: Create audit log entry
- **WHEN** an authenticated user performs an API operation
- **THEN** the system SHALL create an AuditLog record with all fields populated from the request context

#### Scenario: Monthly partition creation
- **WHEN** a new month begins
- **THEN** the system SHALL automatically create a new partition for that month via pg_partman

### Requirement: AuditStore abstract interface
The system SHALL define an `AuditStore` ABC with methods: `write(event)`, `query(filters)`, `export(format, filters)`, `archive(before_date)`. The default implementation SHALL be `DatabaseAuditStore` writing to the partitioned PostgreSQL table.

#### Scenario: Write audit event via store
- **WHEN** an audit event is produced
- **THEN** the system SHALL write it through the AuditStore ABC, allowing alternative implementations without changing business logic

#### Scenario: Query audit logs with filters
- **WHEN** a GET request is sent to `/api/audit-logs` with query parameters `org_id`, `workspace_id`, `user_id`, `action`, `resource_type`, `resource_id`, `success`, `start_time`, `end_time`
- **THEN** the system SHALL return paginated results matching all provided filters

### Requirement: AuditMiddleware for automatic API capture
The system SHALL register a FastAPI `BaseHTTPMiddleware` that captures every API request (excluding `/health`, `/metrics`, OPTIONS requests, and static assets). The middleware SHALL extract `AuthContext` for `user_id`, `org_id`, `workspace_id`, record request method, path, response status, and enqueue the audit event asynchronously.

#### Scenario: Successful API request captured
- **WHEN** an authenticated POST request to `/api/agents` returns 201
- **THEN** the middleware SHALL create an audit event with `action="agent.create"`, `success=true`, `response_status=201`

#### Scenario: Failed API request captured
- **WHEN** an authenticated DELETE request to `/api/agents/{id}` returns 404
- **THEN** the middleware SHALL create an audit event with `success=false`, `response_status=404`, `error_code="NOT_FOUND"`

#### Scenario: Unauthenticated request excluded from audit
- **WHEN** an unauthenticated request hits any endpoint
- **THEN** the middleware SHALL create an audit event with `user_id=NULL`, `action="api.unauthenticated"`

### Requirement: Async batch writer for audit events
The system SHALL use an `asyncio.Queue`-based batch writer that drains audit events from the queue and inserts them into the database in batches (configurable batch size, default 100). The writer SHALL NOT block the request path.

#### Scenario: High-throughput audit writing
- **WHEN** 1000 audit events are produced within 1 second
- **THEN** the batch writer SHALL accumulate and insert them in batches without blocking any API request

### Requirement: Audit action taxonomy with 6 modules
The system SHALL define action types organized into 6 modules: AUTH (login, logout, password change, API key CRUD, permission changes), AGENT (CRUD, deploy, execute), WORKFLOW (CRUD, execute, version management), KNOWLEDGE (knowledge base CRUD, document operations, query), TOOL (register, update, execute), SYSTEM (user CRUD, workspace CRUD, settings, rate limit triggers). Each action SHALL use dot-notation (e.g., `"agent.create"`, `"auth.login.success"`).

#### Scenario: Action type validation
- **WHEN** an audit event is created with action `"agent.create"`
- **THEN** the system SHALL validate that the action is a recognized action type within the AGENT module

### Requirement: Audit log export
The system SHALL support exporting audit logs via `GET /api/audit-logs/export` with `format` parameter (csv or json) and all query filters. The export SHALL be limited to 100,000 records per request.

#### Scenario: Export audit logs as CSV
- **WHEN** a GET request is sent to `/api/audit-logs/export?format=csv&start_time=2026-06-01&end_time=2026-06-10`
- **THEN** the system SHALL return a CSV file with all matching audit log records

### Requirement: Audit log cold archival to MinIO
The system SHALL support configuring a retention threshold (default 365 days). Partitions older than the threshold SHALL be archived to MinIO as compressed JSON files and then dropped from PostgreSQL.

#### Scenario: Archive old audit logs
- **WHEN** a partition is older than the configured retention threshold
- **THEN** the system SHALL export the partition data to MinIO and drop the partition from PostgreSQL

### Requirement: Audit security policy engine
The system SHALL implement a rule-based security policy engine that evaluates audit events against configurable policies. The system SHALL include 3 built-in policies: `bulk_delete_protection` (alert when same user deletes 5+ resources in 1 minute), `off_hours_sensitive_ops` (alert when sensitive operations occur outside configured business hours), `unusual_ip_detection` (alert when login from IP not in user's recent history). Policy violations SHALL be logged as structured warnings.

#### Scenario: Bulk delete detected
- **WHEN** a user performs 5 or more delete operations within 1 minute
- **THEN** the system SHALL log a security warning with policy name `"bulk_delete_protection"` and the user's details

#### Scenario: Off-hours sensitive operation
- **WHEN** a workspace delete operation occurs at 2:00 AM on a Sunday
- **THEN** the system SHALL log a security warning with policy name `"off_hours_sensitive_ops"`

### Requirement: Audit log statistics API
The system SHALL provide `GET /api/audit-logs/stats` with `group_by` parameter (action, user, resource_type) and time range filters. Returns aggregated counts per group.

#### Scenario: Statistics grouped by action
- **WHEN** a GET request is sent to `/api/audit-logs/stats?group_by=action&start_time=2026-06-01`
- **THEN** the system SHALL return a list of action types with their occurrence counts in the specified time range

