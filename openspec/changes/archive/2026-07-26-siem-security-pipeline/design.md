## Context

Hecate has three independent security audit sources that evolved at different times:

1. **AuditLog** (P2, `services/audit/`) — API-level audit via `AuditMiddleware`. Every HTTP request captured. Partitioned PostgreSQL table. Has a `PolicyEngine` that detects anomalies (bulk delete, off-hours, unusual IP) but only `log.warning()` — violations are lost.

2. **SecurityAudit** (9.14, `models/security_audit.py` + `engine/audit_sink.py` + `services/security/audit_service.py`) — Tool policy decision audit. Every `ToolPolicyPipeline` and `ToolAccessPolicy` evaluation emits a structured event. Async batch writer. Just merged, no external consumers.

3. **TraceModel** (OTel bridge) — operational spans, not directly security-relevant but provides latency and tool execution telemetry.

None of these can export to external SIEM systems (Splunk, Datadog, Elastic, QRadar). Enterprise SOC teams have no visibility into Hecate security events. The naming is also confusing — "SecurityAudit" vs "AuditLog" are indistinguishable by name, and "Policy" is overloaded (ToolPolicyPipeline, ToolAccessPolicy, PolicyEngine = three different meanings).

**Industry naming patterns:**
- AWS: CloudTrail (activity) / GuardDuty Finding (anomaly) / Security Hub (aggregation)
- OCSF: Activity (class 4001) / Authorization (class 2201) / Security Finding (class 2001)
- Kubernetes: Audit Log / Admission Decision / Policy Violation

## Goals / Non-Goals

**Goals:**
- Rename SecurityAudit → ToolDecision and PolicyEngine → FindingEngine to eliminate confusion
- Persist PolicyEngine findings (currently lost via `log.warning()`)
- Build a unified SIEM export pipeline with Webhook + Syslog + OCSF support
- Configurable event filtering (by type and severity threshold)
- Backward-compatible defaults (SIEM export disabled by default, no breaking runtime behavior)

**Non-Goals:**
- Real-time blocking based on SIEM feedback (SIEM is observation-only)
- Kafka/ message queue export (defer to P1 — webhook + syslog covers 90% of deployments)
- OTel Logs OTLP export (defer to P2 — requires OTel collector setup)
- Custom SIEM correlation rules inside Hecate (external SIEM does correlation)
- Merging AuditLog and ToolDecision into a single table (different schemas, different query patterns)

## Decisions

### D1: Naming — three layers aligned with industry standards

| Layer | Old Name | New Name | Industry Analog |
|-------|----------|----------|-----------------|
| API operations | AuditLog | AuditLog (unchanged) | AWS CloudTrail, OCSF Activity |
| Tool decisions | SecurityAudit | **ToolDecision** | OCSF Authorization, K8s Admission Decision |
| Anomaly detection | PolicyEngine + PolicyViolation | **FindingEngine + SecurityFinding** | AWS GuardDuty Finding, OCSF Finding |
| Export | (none) | **SIEM Export Pipeline** | AWS Security Hub |

**Rationale:** "SecurityAudit" was misleading — it only captures tool policy decisions, not general security. "ToolDecision" is precise. "PolicyEngine" was overloaded with ToolPolicyPipeline/ToolAccessPolicy. "FindingEngine" aligns with GuardDuty Finding, Defender Alert, OCSF Finding class.

**Alternative considered:** Keep names, document the difference. Rejected — documentation cannot fix naming confusion that appears in code, API paths, config keys, and database tables.

### D2: Unified SecurityEvent — normalize at export layer, not storage layer

```
AuditLog (PG table) ──┐
ToolDecision (PG table) ──→ SecurityEventCollector ──→ SIEMExporter(s)
SecurityFinding (PG table) ──┘    (normalize + filter)
```

Each source keeps its own table and schema. The collector reads from all three and normalizes into a `SecurityEvent` dataclass for export only.

**Rationale:** Different sources have different fields (API log has HTTP method/path; tool decision has layer_results/policy_version; finding has severity/metadata). Forcing them into one table loses type-specific query capability or requires a wide sparse table.

**Alternative considered:** Single `security_events` table with `event_type` discriminator. Rejected — AuditLog is already partitioned and in production; migration risk outweighs the benefit.

### D3: Push architecture with async batching

```
Source event ──→ SecurityEventCollector.emit() (non-blocking, in-memory buffer)
                        │
                        ▼ (every batch_size or flush_interval)
                SIEMExporter.export(events: list[SecurityEvent])
```

The collector buffers events and flushes in batches — same pattern as the existing `SecurityAuditService` async batch writer. Each exporter receives the batch and sends it to its target (webhook HTTP POST, syslog TCP stream, etc.).

**Rationale:** Non-blocking emission prevents SIEM export from affecting request latency. Batching amortizes network I/O. Same proven pattern as the audit batch writer.

**Alternative considered:** Real-time per-event streaming. Rejected — high network overhead for high-volume deployments, and SIEM systems prefer batch ingestion.

### D4: Three exporters in P0

| Exporter | Target | Protocol | Use Case |
|----------|--------|----------|----------|
| **WebhookSIEMExporter** | Splunk HEC, Datadog, Elastic, generic | HTTPS POST with JSON body | Cloud SIEM, most common |
| **SyslogSIEMExporter** | QRadar, ArcSight, rsylog | RFC 5424 over TCP/UDP + TLS | On-premise enterprise |
| **OCSFFormatter** | AWS CloudWatch, IBM, next-gen | JSON with OCSF v1.5 schema | Standards-compliant export |

OCSFFormatter is a formatter, not a transport — it wraps another exporter (typically webhook) to produce OCSF-compliant JSON.

**Rationale:** Webhook covers cloud SIEMs (90% of modern deployments). Syslog is essential for on-premise enterprise. OCSF mapping is low-effort (it's just JSON schema) and future-proofs for industry standardization.

**Alternative considered:** Start with webhook only. Rejected — user explicitly requested all three in scope.

### D5: Configurable filtering — event types + severity threshold

```python
SIEM_FILTER_EVENT_TYPES = "api,tool_policy,anomaly"  # comma-separated, default: all
SIEM_MIN_SEVERITY = "info"  # info | low | medium | high | critical, default: info (all)
```

**Severity mapping (built-in defaults):**

| Event Type | Default Severity |
|------------|-----------------|
| API success (2xx) | INFO |
| API client error (4xx) | LOW |
| API server error (5xx) | MEDIUM |
| ToolDecision ALLOW | INFO |
| ToolDecision SANDBOX | MEDIUM |
| ToolDecision DENY | HIGH |
| ToolDecision APPROVAL_REQUIRED | MEDIUM |
| SecurityFinding (low) | LOW |
| SecurityFinding (medium) | MEDIUM |
| SecurityFinding (high) | HIGH |
| SecurityFinding (critical) | CRITICAL |

**Rationale:** Not every successful API GET needs to go to SIEM. Configurable filtering reduces noise and SIEM ingestion costs.

### D6: SecurityFinding persistence — new table, not reuse AuditLog

```sql
CREATE TABLE security_findings (
    id UUID PRIMARY KEY,
    org_id UUID NOT NULL,
    workspace_id UUID,
    user_id UUID,
    rule_name VARCHAR(100) NOT NULL,    -- e.g., "bulk_delete_rule"
    severity VARCHAR(20) NOT NULL,      -- low | medium | high | critical
    message TEXT NOT NULL,
    source_event JSON,                  -- the triggering AuditEvent
    metadata JSON DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL
);
```

**Rationale:** Findings have different fields from AuditLog (rule_name, severity, source_event). Reusing AuditLog would require nullable columns or a wide table. Separate table allows dedicated query API and retention policy.

**Alternative considered:** Store findings as AuditLog entries with `action="security.finding"`. Rejected — loses severity indexing and rule_name filtering.

### D7: Migration — rename table via Alembic

Two migrations:
1. Rename `security_audit_events` → `tool_decisions` (Alembic `rename_table`)
2. Create `security_findings` table

No data migration needed — column names stay the same (only table name changes). Config key renames are in `.env.example` with backward-compatible aliases (if `AGENT_ENV_AUDIT_ENABLED` is set, fall back to it when `AGENT_ENV_DECISION_ENABLED` is not set).

## Risks / Trade-offs

- **[Naming refactor breaks 9.14 code]** → 9.14 merged days ago, no external consumers, no published API docs referencing old names. Migration is code-internal only.
- **[Syslog reliability]** → UDP is lossy; TCP adds backpressure. Mitigation: TCP default, configurable retry, buffer overflow drops oldest with WARNING log.
- **[Webhook endpoint down]** → Network failure to SIEM should not affect Hecate. Mitigation: exporter catches all exceptions, logs error, continues. Failed batches are dropped (not retried) to prevent unbounded memory growth. Retry/queue is a P1 enhancement.
- **[OCSF schema correctness]** → OCSF v1.5 is complex; our mapping may miss required fields. Mitigation: map core fields only (timestamp, severity, actor, action, resource), put platform-specific data in `metadata` extension. Validate against OCSF JSON schema in tests.
- **[Config rename confusion]** → Users with `AGENT_ENV_AUDIT_ENABLED=true` in `.env` may not notice the rename. Mitigation: backward-compatible alias in config loader.

## Migration Plan

1. **Phase 1 — Rename (no behavior change):** Rename all SecurityAudit → ToolDecision in code. Rename table via Alembic. Add config aliases. All existing tests pass with updated names.
2. **Phase 2 — Finding persistence:** Add SecurityFindingModel + FindingEngine persistence. FindingEngine now writes to DB instead of `log.warning()`.
3. **Phase 3 — SIEM Pipeline:** Add SecurityEvent + Collector + Exporters. Disabled by default. No impact on existing behavior.

**Rollback:** Revert the branch. The Alembic migration can be downgraded (`alembic downgrade -1`). Config aliases ensure old `.env` files still work.

## Open Questions

None remaining — all design decisions confirmed during explore phase.
