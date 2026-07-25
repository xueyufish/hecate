## ADDED Requirements

### Requirement: SecurityEvent unified data model
The system SHALL define a `SecurityEvent` dataclass that normalizes events from AuditLog, ToolDecision, and SecurityFinding into a single schema. Each SecurityEvent SHALL contain: event_type (`api` | `tool_policy` | `anomaly`), severity (`info` | `low` | `medium` | `high` | `critical`), source (`audit_log` | `tool_decision` | `security_finding`), timestamp, actor (user_id and/or agent_id), action, decision, resource, and metadata (extensible).

#### Scenario: AuditLog normalized to SecurityEvent
- **WHEN** an AuditLog record with `action="agent.create"` and `success=true` is collected
- **THEN** a SecurityEvent is created with `event_type="api"`, `severity="info"`, `source="audit_log"`

#### Scenario: ToolDecision normalized to SecurityEvent
- **WHEN** a ToolDecision with `decision="DENY"` is collected
- **THEN** a SecurityEvent is created with `event_type="tool_policy"`, `severity="high"`, `source="tool_decision"`

#### Scenario: SecurityFinding normalized to SecurityEvent
- **WHEN** a SecurityFinding with `severity="critical"` is collected
- **THEN** a SecurityEvent is created with `event_type="anomaly"`, `severity="critical"`, `source="security_finding"`

### Requirement: SIEMExporter pluggable interface
The system SHALL define a `SIEMExporter` ABC with an `export(events: list[SecurityEvent])` async method. Multiple exporters MAY be registered simultaneously. The system SHALL provide a `NullSIEMExporter` as the default no-op implementation.

#### Scenario: Multiple exporters receive same events
- **WHEN** a batch of 10 SecurityEvents is flushed
- **THEN** each registered exporter receives the same batch of 10 events

#### Scenario: Exporter failure does not affect other exporters
- **WHEN** the webhook exporter fails to send events
- **THEN** the syslog exporter still receives and processes the batch
- **AND** the error is logged

### Requirement: WebhookSIEMExporter
The system SHALL provide a `WebhookSIEMExporter` that sends SecurityEvent batches as JSON HTTP POST requests to a configurable endpoint. It SHALL support authentication via bearer token or custom headers. It SHALL use configurable batch size and flush interval.

#### Scenario: Splunk HEC format
- **WHEN** `SIEM_WEBHOOK_FORMAT=splunk_hec` and events are flushed
- **THEN** the exporter sends a POST with each event wrapped as `{"event": {...}, "time": timestamp}` to the configured URL with `Authorization: Splunk <token>` header

#### Scenario: Generic JSON format
- **WHEN** `SIEM_WEBHOOK_FORMAT=json` and events are flushed
- **THEN** the exporter sends a POST with `{"events": [...]}` JSON body to the configured URL

#### Scenario: Authentication via bearer token
- **WHEN** `SIEM_WEBHOOK_TOKEN` is set
- **THEN** the exporter adds `Authorization: Bearer <token>` header to all requests

#### Scenario: Retry on transient failure
- **WHEN** the webhook endpoint returns HTTP 503
- **THEN** the exporter retries up to 3 times with exponential backoff (1s, 2s, 4s)
- **AND** logs a warning on each retry

#### Scenario: Drop on permanent failure
- **WHEN** the webhook endpoint returns HTTP 401 after retries
- **THEN** the exporter logs an error and drops the batch (does not block the pipeline)

### Requirement: SyslogSIEMExporter
The system SHALL provide a `SyslogSIEMExporter` that sends SecurityEvents as RFC 5424 syslog messages over TCP or UDP with optional TLS. It SHALL support configurable facility and severity mapping.

#### Scenario: TCP transport
- **WHEN** `SIEM_SYSLOG_PROTOCOL=tcp` and events are flushed
- **THEN** the exporter opens a TCP connection to the configured host:port and sends one syslog message per event

#### Scenario: UDP transport
- **WHEN** `SIEM_SYSLOG_PROTOCOL=udp` and events are flushed
- **THEN** the exporter sends UDP datagrams (no connection state, fire-and-forget)

#### Scenario: TLS encryption
- **WHEN** `SIEM_SYSLOG_TLS=true`
- **THEN** the exporter wraps the TCP connection in TLS with certificate verification (configurable CA bundle)

#### Scenario: RFC 5424 format compliance
- **WHEN** an event with severity HIGH is exported
- **THEN** the syslog message uses PRI calculated as `facility * 8 + severity` where severity maps from SecurityEvent severity (critical=0, high=1, ...)

#### Scenario: Connection failure logged
- **WHEN** the syslog server is unreachable
- **THEN** the exporter logs an error, drops the current batch, and attempts reconnection on the next flush

### Requirement: OCSFFormatter
The system SHALL provide an `OCSFFormatter` that maps SecurityEvents to OCSF v1.5 schema classes. API events map to Activity class (4001), tool decisions map to Authorization class (2201), and findings map to Security Finding class (2001). The formatter is a transformation layer that wraps another exporter (typically webhook).

#### Scenario: API event mapped to OCSF Activity
- **WHEN** a SecurityEvent with `event_type="api"` is formatted
- **THEN** the output JSON contains `class_uid: 4001`, `activity_name`, `actor.user`, `time`, and `severity_id`

#### Scenario: Tool decision mapped to OCSF Authorization
- **WHEN** a SecurityEvent with `event_type="tool_policy"` and `decision="DENY"` is formatted
- **THEN** the output JSON contains `class_uid: 2201`, `decision="deny"`, `action_id`, and `actor` fields

#### Scenario: Finding mapped to OCSF Security Finding
- **WHEN** a SecurityEvent with `event_type="anomaly"` is formatted
- **THEN** the output JSON contains `class_uid: 2001`, `finding_info`, `severity_id`, and `resources`

### Requirement: SecurityEventCollector
The system SHALL provide a `SecurityEventCollector` that subscribes to AuditLog, ToolDecision, and SecurityFinding event streams. The collector normalizes events into SecurityEvent, applies configurable filtering, and routes to registered SIEMExporters via async batch flushing.

#### Scenario: Event from AuditMiddleware collected
- **WHEN** the AuditMiddleware produces an event for `POST /api/agents`
- **THEN** the collector normalizes it into a SecurityEvent and buffers it

#### Scenario: Event from ToolDecisionEmitter collected
- **WHEN** the ToolDecisionEmitter produces a DENY event for tool `bash`
- **THEN** the collector normalizes it into a SecurityEvent with severity HIGH and buffers it

#### Scenario: Event from FindingEngine collected
- **WHEN** the FindingEngine persists a SecurityFinding
- **THEN** the collector normalizes it into a SecurityEvent and buffers it

#### Scenario: Event type filtering
- **WHEN** `SIEM_FILTER_EVENT_TYPES=tool_policy,anomaly` (API events excluded)
- **THEN** the collector skips AuditLog events and only processes ToolDecision and SecurityFinding events

#### Scenario: Severity threshold filtering
- **WHEN** `SIEM_MIN_SEVERITY=medium`
- **THEN** the collector only buffers events with severity MEDIUM, HIGH, or CRITICAL
- **AND** INFO and LOW severity events are silently dropped

#### Scenario: Batch flush
- **WHEN** the collector's buffer reaches `SIEM_BATCH_SIZE` events
- **THEN** all buffered events are flushed to registered exporters
- **AND** the buffer is cleared

#### Scenario: Time-based flush
- **WHEN** `SIEM_FLUSH_INTERVAL` seconds elapse since the last flush
- **THEN** all buffered events are flushed regardless of buffer size

### Requirement: SIEM pipeline disabled by default
The system SHALL default to `SIEM_ENABLED=false`. When disabled, the collector is not started, no exporters are registered, and events are not collected or exported. The pipeline startup and shutdown SHALL be managed by the application lifespan.

#### Scenario: No overhead when disabled
- **WHEN** `SIEM_ENABLED=false`
- **THEN** no SecurityEventCollector is instantiated
- **AND** no event normalization or buffering occurs

#### Scenario: Startup on enable
- **WHEN** `SIEM_ENABLED=true` and the application starts
- **THEN** the collector initializes, registers configured exporters (webhook/syslog/ocsf), and begins collecting events

#### Scenario: Graceful shutdown flushes buffer
- **WHEN** the application shuts down with events still in the buffer
- **THEN** the collector flushes all remaining events before shutdown completes
