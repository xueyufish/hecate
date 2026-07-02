## Context

Hecate is an enterprise-grade, self-hosted, model-agnostic Agent platform. P2 (Enterprise-Ready) is at 49/57 (86%). This change delivers 4 backend features to push P2 to 53/57 (93%).

**Current state**:
- Evaluation framework (7.1/7.2): Evaluator ABC, 4 RAG evaluators (Ragas-backed), 3 Agent evaluators (LLM-as-Judge), EvaluationEngine, ORM models, and API are all implemented. However, `EvaluationEngine.run()` hardcodes `generated_answer=""`, making all scores meaningless. `EvaluationItemModel` lacks a `generated_answer` field. `ToolCallAccuracyEvaluator` and `TaskCompletionEvaluator` are missing.
- Audit Logs (8.7): No AuditLog model, no audit middleware, no audit API. Existing observability infrastructure (TraceModel, EventStore, StructuredLogger) covers execution tracing, not user operation auditing.
- Scheduled Tasks (13.9): `MetaAgentScheduler` exists for meta-agents only (simple interval-based, no cron, no multi-node). No ScheduledTask model, no cron support, no agent/workflow binding.

**Architecture constraints**:
- Engine layer has zero external deps (except jsonschema). New features live in services/ and api/ layers.
- Multi-tenant hierarchy: OrganizationModel → WorkspaceModel → Resources.
- AuthContext provides `user_id`, `org_id`, `workspace_id`, `role` via FastAPI dependency injection.
- All ORM models inherit BaseModel (UUID PK, `created_at`/`updated_at`, soft-delete `deleted`/`deleted_at`).
- `metadata_` alias pattern for SQLAlchemy models.

## Goals / Non-Goals

**Goals:**
- Fix evaluation engine to support actual answer generation (manual + RAG pipeline integration)
- Add 2 missing agent evaluation metrics (ToolCallAccuracy, TaskCompletion)
- Deliver SaaS-ready audit logging with 6 modules, ~70 action types, monthly partitioning, MinIO archival, and security policy engine
- Deliver multi-node scheduled task execution with cron expressions, PostgreSQL JobStore, and advisory locks

**Non-Goals:**
- Frontend UI for any of these features (Canvas, Model Playground are separate P2 frontend work)
- ML-based anomaly detection for audit logs (P3)
- Temporal integration for scheduled tasks (P3)
- 40+ evaluator expansion (P3 feature 7.2a)
- Compliance reporting (SOC 2, GDPR templates) — infrastructure is laid but templates are P3

## Decisions

### D1: Audit Store Architecture — ABC + Single Table + Partitioning

**Decision**: `AuditStore` ABC with `DatabaseAuditStore` as default implementation. PostgreSQL monthly range partitioning on `created_at`. MinIO archival for partitions older than configurable threshold.

**Rationale**: 
- ABC allows future `DualAuditStore` (PG + Elasticsearch) or `ExternalAuditStore` (SLS, Datadog) without touching business logic
- Monthly partitioning handles 100K-1M logs/day efficiently; individual partitions can be dropped for retention
- MinIO is already in the infrastructure stack (docker-compose)

**Alternatives considered**:
- Single unpartitioned table: simpler but degrades at scale; VACUUM overhead grows
- Elasticsearch as primary store: powerful but adds operational complexity for self-hosted users
- Salesforce-style 3-layer storage (ELF + ELO + RTEM): overkill for self-hosted; our single table + partition + archival achieves equivalent capabilities

### D2: Audit Capture — FastAPI Middleware + Service Decorators

**Decision**: Two-layer capture:
1. `AuditMiddleware` (BaseHTTPMiddleware) — captures all API requests automatically, extracts AuthContext, records method/path/status/timing
2. `@audit_action(action=...)` decorator — enriches with resource_type, resource_id, business metadata at service level

**Rationale**: Middleware gives full coverage (no route can bypass auditing). Decorators add semantic enrichment. Dify only captures at service level (incomplete coverage). Salesforce captures at platform level (not possible for self-hosted).

**Exclusions**: `/health`, `/metrics`, static assets, OPTIONS requests.

### D3: Scheduled Tasks — APScheduler + PostgreSQL JobStore + Advisory Locks

**Decision**: APScheduler 3.x with `PostgreSQLJobStore` for job persistence. Multiple scheduler instances (one per app node) compete for job execution via `pg_try_advisory_lock()`.

**Rationale**:
- APScheduler is lightweight, no external service dependency (unlike Celery+Redis or Temporal)
- PostgreSQL JobStore persists jobs across restarts
- Advisory locks prevent duplicate execution in multi-node deployments
- APScheduler natively supports cron expressions, intervals, and one-shot triggers

**Alternatives considered**:
- Celery Beat + Redis: heavier infrastructure requirement; Redis is not in current stack
- Temporal: already in codebase as placeholder, but full integration is P3
- Single scheduler + multiple workers: single point of failure

### D4: Evaluation Engine Fix — Dual Mode (Manual + Pipeline)

**Decision**: `EvaluationEngine.run()` accepts an optional `answer_source` parameter:
- `"manual"` — uses `generated_answer` from `EvaluationItemModel` (new field)
- `"pipeline"` — invokes RAG pipeline or Agent session to generate answers before evaluation
- `"auto"` (default) — uses `generated_answer` if present, falls back to pipeline invocation

**Rationale**: Supports both pre-labeled datasets (regression testing) and live pipeline evaluation (quality monitoring). The `generated_answer` field addition is backward-compatible (nullable, default empty).

### D5: Audit Security Policy Engine — Rule-Based (P2) → ML-Based (P3)

**Decision**: P2 implements a simple rule engine with 3 built-in policies:
1. `bulk_delete_protection` — alert when same user deletes 5+ resources in 1 minute
2. `off_hours_sensitive_ops` — alert when sensitive operations occur outside business hours
3. `unusual_ip_detection` — alert when login from unrecognized IP

Policies are evaluated synchronously against each audit event in the async writer. Matches trigger structured log warnings (and optional webhook notifications in P3).

**Rationale**: Rule-based policies cover the most common enterprise security concerns. ML anomaly detection requires training data and is better suited for P3 when audit data has accumulated.

### D6: Audit Log Partitioning — Manual with pg_partman

**Decision**: Use `pg_partman` extension for automatic monthly partition creation. Include setup in Alembic migration. Retention policy drops partitions older than configurable threshold (default 365 days).

**Rationale**: pg_partman is a well-maintained PostgreSQL extension that automates partition lifecycle. Ships with PostgreSQL 16 contrib or as standalone extension.

## Risks / Trade-offs

- **[Risk] Audit middleware adds latency to every API request** → Mitigation: async batch writer with `asyncio.Queue`; middleware only enqueues, never blocks on DB write. Target: <1ms overhead per request.
- **[Risk] APScheduler advisory lock contention at high scale** → Mitigation: advisory locks are lightweight (integer comparison); contention only occurs at schedule trigger time (typically once per minute/hour). If this becomes a bottleneck, migrate to Temporal in P3.
- **[Risk] pg_partman not available in all PostgreSQL deployments** → Mitigation: provide manual partition creation as fallback; document both approaches.
- **[Risk] RAG pipeline integration may be slow for large datasets** → Mitigation: run evaluation in background with progress tracking; support per-item timeout.
- **[Trade-off] Single audit table limits query flexibility vs separate tables per module** → Accepted: partitioning + proper indexes provide sufficient query performance for self-hosted scale. A single table simplifies retention management and archival.

## Migration Plan

1. **Alembic migration**: Add `generated_answer` column to `evaluation_items`, create `audit_logs` (partitioned), `scheduled_tasks`, `scheduled_task_executions` tables, install `pg_partman` extension.
2. **Deploy**: New middleware registered in `main.py`, APScheduler started in app lifespan.
3. **Rollback**: Remove middleware from `main.py`, drop new tables/columns. No data corruption risk since these are additive changes.
4. **No breaking changes**: All changes are additive. Existing evaluation API continues to work (new `generated_answer` column is nullable).

## Open Questions

- Should audit log archival to MinIO be synchronous or asynchronous? (Leaning async — archival worker runs on schedule)
- Should `AuditSecurityPolicy` violations be stored in a separate table or just logged? (Leaning: logged + optional webhook, separate table in P3)
- Should scheduled task executions support retry with backoff? (Leaning: yes, configurable `max_retries` + `retry_delay`)
