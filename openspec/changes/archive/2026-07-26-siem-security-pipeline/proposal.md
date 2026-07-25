## Why

Hecate has three security audit sources (API-level AuditLog, tool-level SecurityAudit, anomaly-detection PolicyEngine) but **no way to export events to external SIEM systems**. Additionally, PolicyEngine violations are only `log.warning()` — they are lost entirely. The naming is also confusing: "SecurityAudit" and "AuditLog" both contain "Audit" but capture fundamentally different things, and "Policy" is overloaded across ToolPolicyPipeline, ToolAccessPolicy, and PolicyEngine with three different meanings.

This change unifies the three sources into a coherent SIEM export pipeline, fixes the finding-persistence gap, and aligns naming with industry standards (AWS CloudTrail / GuardDuty / Security Hub, OCSF schema classes).

## What Changes

### Naming Refactor (BREAKING — 9.14 just merged, no external consumers)

- `SecurityAuditModel` → **`ToolDecisionModel`** (table `security_audit_events` → `tool_decisions`)
- `SecurityAuditEmitter` / `AuditSink` → **`ToolDecisionEmitter`** / **`DecisionSink`**
- `SecurityAuditService` → **`ToolDecisionService`**
- `SecurityAuditReadSchema` / `SecurityAuditQuerySchema` → **`ToolDecisionReadSchema`** / **`ToolDecisionQuerySchema`**
- API: `GET /api/security/audit` → **`GET /api/security/decisions`**
- Config: `AGENT_ENV_AUDIT_*` → **`AGENT_ENV_DECISION_*`**
- `PolicyViolation` → **`SecurityFinding`**
- `PolicyEngine` → **`FindingEngine`**
- `AuditSecurityPolicy` ABC → **`DetectionRule`** ABC
- `PolicyContext` → **`DetectionContext`**
- `PolicySeverity` → **`FindingSeverity`**
- `BulkDeleteProtectionPolicy` → **`BulkDeleteRule`**
- `OffHoursSensitiveOpsPolicy` → **`OffHoursRule`**
- `UnusualIPDetectionPolicy` → **`UnusualIPRule`**

### Finding Persistence (fixes lost violations)

- New `SecurityFindingModel` table — persists FindingEngine violations instead of discarding them via `log.warning()`
- REST API: `GET /api/security/findings` for querying persisted findings
- Findings feed into the SIEM export pipeline as high-severity events

### SIEM Export Pipeline (new capability)

- **`SecurityEvent`** unified dataclass — normalizes AuditLog + ToolDecision + SecurityFinding into one schema
- **`SIEMExporter`** ABC — pluggable export sink interface
- **`WebhookSIEMExporter`** — HTTPS POST (Splunk HEC, Datadog, Elastic, generic JSON)
- **`SyslogSIEMExporter`** — RFC 5424 over TCP/UDP with optional TLS
- **`OCSFFormatter`** — OCSF v1.5 schema mapping (Activity class 4001, Authorization class 2201, Finding class 2001)
- **`SecurityEventCollector`** — subscribes to all three sources, normalizes, applies configurable filtering (event types + severity threshold), routes to registered exporters
- Config: `SIEM_ENABLED`, `SIEM_EXPORTERS`, `SIEM_WEBHOOK_URL`, `SIEM_SYSLOG_HOST/PORT/PROTOCOL`, `SIEM_MIN_SEVERITY`, `SIEM_FILTER_EVENT_TYPES`, `SIEM_BATCH_SIZE`, `SIEM_FLUSH_INTERVAL`

## Capabilities

### New Capabilities

- `tool-decision-log`: Tool policy decision audit — renamed from structured-security-audit. Captures ALLOW/DENY/SANDBOX decisions from ToolPolicyPipeline and ToolAccessPolicy. Persists to `tool_decisions` table with async batch writer.
- `security-findings`: Anomaly detection finding persistence — stores FindingEngine violations (bulk delete, off-hours ops, unusual IP) in `security_findings` table. Provides REST query API. Replaces the current `log.warning()` discard pattern.
- `siem-export`: SIEM export pipeline — unifies AuditLog, ToolDecision, and SecurityFinding events into normalized SecurityEvent stream. Exports via Webhook (JSON), Syslog (RFC 5424), and OCSF v1.5 formatter. Configurable filtering by event type and severity.

### Modified Capabilities

- `audit-logs`: Rename PolicyEngine → FindingEngine, PolicyViolation → SecurityFinding, AuditSecurityPolicy → DetectionRule, PolicyContext → DetectionContext, PolicySeverity → FindingSeverity. Built-in rules renamed (BulkDeleteProtectionPolicy → BulkDeleteRule, etc.). FindingEngine now persists violations to SecurityFindingModel instead of logging and discarding.

## Impact

**Files created (~15):**
- `src/hecate/models/tool_decision.py` (rename from security_audit.py)
- `src/hecate/models/security_finding.py` (new)
- `src/hecate/engine/decision_sink.py` (rename from audit_sink.py)
- `src/hecate/services/security/decision_service.py` (rename from audit_service.py)
- `src/hecate/services/security/finding_service.py` (new)
- `src/hecate/services/security/siem/event.py` (new — SecurityEvent)
- `src/hecate/services/security/siem/exporter.py` (new — SIEMExporter ABC)
- `src/hecate/services/security/siem/webhook.py` (new)
- `src/hecate/services/security/siem/syslog.py` (new)
- `src/hecate/services/security/siem/ocsf.py` (new)
- `src/hecate/services/security/siem/collector.py` (new)
- `src/hecate/api/tool_decisions.py` (rename from security_audit.py)
- `src/hecate/api/security_findings.py` (new)
- `alembic/versions/xxx_rename_security_audit_to_tool_decisions.py` (migration)
- `alembic/versions/yyy_add_security_findings.py` (migration)

**Files modified (~10):**
- `src/hecate/core/config.py` — rename AGENT_ENV_AUDIT_* → AGENT_ENV_DECISION_*, add SIEM_* settings
- `src/hecate/engine/policy_pipeline.py` — update emitter references
- `src/hecate/engine/tool_access.py` — update emitter references
- `src/hecate/engine/workers/tool_worker.py` — update emitter references
- `src/hecate/services/audit/policy.py` — rename to finding.py or keep with updated names
- `src/hecate/services/audit/service.py` — update FindingEngine references
- `src/hecate/main.py` — update DI wiring for renamed services + SIEM collector startup
- `.env.example` — rename config keys, add SIEM settings
- `docs/design/security-architecture.md` — update naming

**Dependencies:** None new. Uses existing httpx (webhook), standard library logging (syslog), and Pydantic (event models).
