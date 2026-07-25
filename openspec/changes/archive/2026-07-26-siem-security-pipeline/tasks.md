# Implementation Tasks

## 1. Naming Refactor: SecurityAudit → ToolDecision

- [x] 1.1 Rename `src/hecate/models/security_audit.py` → `src/hecate/models/tool_decision.py`; rename `SecurityAuditModel` → `ToolDecisionModel`, `SecurityAuditReadSchema` → `ToolDecisionReadSchema`, `SecurityAuditQuerySchema` → `ToolDecisionQuerySchema`
- [x] 1.2 Rename `src/hecate/engine/audit_sink.py` → `src/hecate/engine/decision_sink.py`; rename `AuditSink` → `DecisionSink`, `NullAuditSink` → `NullDecisionSink`, `SecurityAuditEmitter` → `ToolDecisionEmitter`, `audit_emitter` → `decision_emitter`
- [x] 1.3 Rename `src/hecate/services/security/audit_service.py` → `src/hecate/services/security/decision_service.py`; rename `SecurityAuditService` → `ToolDecisionService`; update class to implement `DecisionSink` ABC
- [x] 1.4 Rename `src/hecate/api/security_audit.py` → `src/hecate/api/tool_decisions.py`; update route path `/api/security/audit` → `/api/security/decisions`
- [x] 1.5 Update all import references in `engine/policy_pipeline.py`, `engine/tool_access.py`, `engine/workers/tool_worker.py` to use new `decision_emitter` and `DecisionSink`
- [x] 1.6 Update `core/config.py`: rename `AGENT_ENV_AUDIT_*` settings → `AGENT_ENV_DECISION_*`; add backward-compatible alias resolution (legacy key falls back when new key not set)
- [x] 1.7 Update `.env.example`: rename config keys with comment noting legacy aliases
- [x] 1.8 Create Alembic migration to rename table `security_audit_events` → `tool_decisions` (column names unchanged)
- [x] 1.9 Update `main.py` DI wiring to use `ToolDecisionService` and `decision_emitter`
- [x] 1.10 Update all existing tests: rename imports, class references, API endpoint paths, table names
- [x] 1.11 Run ruff + mypy + pytest to verify rename is complete with zero errors

## 2. Naming Refactor: PolicyEngine → FindingEngine

- [x] 2.1 Rename in `src/hecate/services/audit/policy.py`: `PolicyEngine` → `FindingEngine`, `AuditSecurityPolicy` → `DetectionRule`, `PolicyViolation` → `SecurityFinding`, `PolicyContext` → `DetectionContext`, `PolicySeverity` → `FindingSeverity`
- [x] 2.2 Rename built-in rules: `BulkDeleteProtectionPolicy` → `BulkDeleteRule`, `OffHoursSensitiveOpsPolicy` → `OffHoursRule`, `UnusualIPDetectionPolicy` → `UnusualIPRule`
- [x] 2.3 Update `src/hecate/services/audit/service.py` to use `FindingEngine` and `SecurityFinding`
- [x] 2.4 Update all import references across the codebase
- [x] 2.5 Update existing audit policy tests with new names
- [x] 2.6 Run ruff + mypy + pytest to verify rename is complete

## 3. SecurityFinding Persistence

- [x] 3.1 Create `src/hecate/models/security_finding.py`: `SecurityFindingModel` ORM (table `security_findings`) with fields: id, org_id, workspace_id, user_id, rule_name, severity, message, source_event (JSON), metadata (JSON), created_at; indexes on (severity, created_at) and (rule_name, created_at)
- [x] 3.2 Create Pydantic schemas: `SecurityFindingReadSchema`, `SecurityFindingQuerySchema`
- [x] 3.3 Create Alembic migration for `security_findings` table
- [x] 3.4 Modify `FindingEngine.evaluate()` to persist findings to `SecurityFindingModel` instead of `log.warning()`; keep DEBUG-level logging for operational visibility
- [x] 3.5 Create `src/hecate/services/security/finding_service.py`: `SecurityFindingService` with `query()`, `get_by_id()`, and retention cleanup methods
- [x] 3.6 Create `src/hecate/api/security_findings.py`: REST API `GET /api/security/findings` with filtering by org_id, workspace_id, user_id, rule_name, severity, time range + pagination
- [x] 3.7 Wire finding service + API in `main.py` DI
- [x] 3.8 Add retention cleanup task for SecurityFinding (default 90 days, configurable `SECURITY_FINDING_RETENTION_DAYS`)
- [x] 3.9 Write tests: model tests, service tests, API tests, FindingEngine persistence integration test
- [x] 3.10 Run ruff + mypy + pytest

## 4. SIEM Export: SecurityEvent + Collector

- [x] 4.1 Create `src/hecate/services/security/siem/__init__.py`
- [x] 4.2 Create `src/hecate/services/security/siem/event.py`: `SecurityEvent` dataclass with fields: event_type, severity, source, timestamp, actor_user_id, actor_agent_id, action, decision, resource, metadata; `Severity` enum (INFO, LOW, MEDIUM, HIGH, CRITICAL)
- [x] 4.3 Create severity mapping function: maps AuditLog events (success/failure → severity), ToolDecision events (ALLOW/SANDBOX/DENY → severity), SecurityFinding events (FindingSeverity → SecurityEvent severity)
- [x] 4.4 Create `src/hecate/services/security/siem/exporter.py`: `SIEMExporter` ABC with `async export(events: list[SecurityEvent])` method; `NullSIEMExporter` no-op default
- [x] 4.5 Create `src/hecate/services/security/siem/collector.py`: `SecurityEventCollector` — subscribes to AuditLog events, ToolDecisionEmitter, and FindingEngine; normalizes to SecurityEvent; applies event_type + severity filtering; buffers and flushes to exporters via async batch
- [x] 4.6 Wire collector into AuditMiddleware event flow (emit to collector after audit write)
- [x] 4.7 Wire collector into ToolDecisionEmitter (emit to collector after decision emit)
- [x] 4.8 Wire collector into FindingEngine (emit to collector after finding persistence)
- [x] 4.9 Add SIEM config to `core/config.py`: `SIEM_ENABLED` (default false), `SIEM_EXPORTERS`, `SIEM_FILTER_EVENT_TYPES`, `SIEM_MIN_SEVERITY`, `SIEM_BATCH_SIZE` (default 50), `SIEM_FLUSH_INTERVAL` (default 5.0)
- [x] 4.10 Wire SIEM collector startup/shutdown in `main.py` lifespan
- [x] 4.11 Write tests: SecurityEvent normalization tests, severity mapping tests, collector buffer/flush tests, filtering tests
- [x] 4.12 Run ruff + mypy + pytest

## 5. SIEM Export: Webhook Exporter

- [x] 5.1 Create `src/hecate/services/security/siem/webhook.py`: `WebhookSIEMExporter` — async HTTP POST via httpx; supports `splunk_hec` and `json` formats; bearer token auth; configurable headers
- [x] 5.2 Implement retry logic: 3 retries with exponential backoff (1s, 2s, 4s) on HTTP 5xx; drop batch on 4xx with error log
- [x] 5.3 Add webhook config: `SIEM_WEBHOOK_URL`, `SIEM_WEBHOOK_TOKEN`, `SIEM_WEBHOOK_FORMAT` (json | splunk_hec), `SIEM_WEBHOOK_HEADERS` (JSON)
- [x] 5.4 Write tests: webhook format tests (json + splunk_hec), auth header tests, retry tests, drop-on-failure tests
- [x] 5.5 Run ruff + mypy + pytest

## 6. SIEM Export: Syslog Exporter

- [x] 6.1 Create `src/hecate/services/security/siem/syslog.py`: `SyslogSIEMExporter` — RFC 5424 message format; TCP and UDP transport; optional TLS
- [x] 6.2 Implement RFC 5424 message construction: PRI (facility * 8 + severity), VERSION=1, TIMESTAMP, HOSTNAME, APPNAME, PROCID, MSGID, STRUCTURED-DATA, MSG
- [x] 6.3 Implement TCP transport with connection pooling; reconnection on failure
- [x] 6.4 Implement UDP transport (fire-and-forget datagrams)
- [x] 6.5 Implement TLS wrapping for TCP mode (configurable CA bundle, optional client cert)
- [x] 6.6 Add syslog config: `SIEM_SYSLOG_HOST`, `SIEM_SYSLOG_PORT` (default 514), `SIEM_SYSLOG_PROTOCOL` (tcp | udp), `SIEM_SYSLOG_TLS` (default false), `SIEM_SYSLOG_FACILITY` (default 4 = security/authorization)
- [x] 6.7 Write tests: RFC 5424 format compliance tests, TCP connection tests, UDP send tests, TLS tests (mock), connection failure handling tests
- [x] 6.8 Run ruff + mypy + pytest

## 7. SIEM Export: OCSF Formatter

- [x] 7.1 Create `src/hecate/services/security/siem/ocsf.py`: `OCSFFormatter` — transforms SecurityEvent into OCSF v1.5 compliant JSON
- [x] 7.2 Implement OCSF Activity class (4001) mapping for API events: `activity_name`, `actor.user.uid`, `actor.user.name`, `time`, `severity_id`, `status_id`, `resources`
- [x] 7.3 Implement OCSF Authorization class (2201) mapping for tool decision events: `decision`, `action_id`, `actor.agent`, `resource.tool`, `policy`
- [x] 7.4 Implement OCSF Security Finding class (2001) mapping for anomaly events: `finding_info.title`, `finding_info.uid`, `severity_id`, `resources`, `time`
- [x] 7.5 Implement OCSF wrapper: formatter wraps another exporter (decorator pattern), transforming events before delegating to the wrapped exporter's `export()` method
- [x] 7.6 Write tests: OCSF schema field presence tests for all 3 classes, severity_id mapping tests, actor field mapping tests
- [x] 7.7 Run ruff + mypy + pytest

## 8. Integration Tests + Documentation

- [x] 8.1 E2E: ToolDecision → collector → exporter flow
- [x] 8.2 E2E: FindingEngine finding → SIEM export flow
- [x] 8.3 E2E: Multiple exporters receive same events + exporter failure isolation
- [x] 8.4 Filtering integration test: severity threshold + event type filter
- [x] 8.5 Disabled SIEM: emit_to_siem no-op when collector is None
- [x] 8.6 Graceful shutdown: buffer flushed on stop
- [x] 8.7 `.env.example` updated with all SIEM_* and AGENT_ENV_DECISION_* settings
- [x] 8.8 Update `docs/design/security-architecture.md` with SIEM Pipeline section
- [x] 8.9 Run full test suite (ruff + mypy + pytest) — 136 passed, 4 skipped, zero errors
- [x] 8.10 Verify spec deltas match implementation behavior
