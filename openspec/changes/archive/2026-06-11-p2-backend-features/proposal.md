## Why

P2 (Enterprise-Ready) is at 49/57 (86%). Four backend features remain before the remaining work shifts to frontend-only (Canvas, Model Playground). Completing these now brings P2 to 53/57 (93%) and unblocks P3 evaluation expansion.

Additionally, current evaluation framework has evaluators implemented but the end-to-end flow is broken — `EvaluationEngine.run()` hardcodes `generated_answer=""`, making all evaluation scores meaningless against empty strings.

## What Changes

- **7.1 RAG Evaluation (fix)**: Wire `EvaluationEngine` to RAG Pipeline for automatic answer generation; add `generated_answer` field to `EvaluationItemModel` so users can also provide answers manually.
- **7.2 Agent Evaluation (fix + extend)**: Add `ToolCallAccuracyEvaluator` and `TaskCompletionEvaluator` (spec references "task completion rate, tool call accuracy, response quality"); share the `generated_answer` fix from 7.1.
- **8.7 Audit Logs (new)**: SaaS-ready audit logging — `AuditStore` ABC + PostgreSQL monthly-partitioned table + async batch writer + MinIO cold archival + `AuditSecurityPolicy` rule engine (3 built-in policies) + REST API for query/export. Full-capture of all API operations via FastAPI middleware, organized into 6 modules (AUTH, AGENT, WORKFLOW, KNOWLEDGE, TOOL, SYSTEM) with ~70 action types. Inspired by Salesforce Event Monitoring + Transaction Security, scaled for self-hosted.
- **13.9 Scheduled Tasks (new)**: Cron-triggered Agent/Workflow execution with multi-node support — `APScheduler` + PostgreSQL JobStore + advisory locks for distributed scheduling, cron expression support, schedule state machine (ACTIVE/PAUSED/COMPLETED), `max_concurrent_runs` and `catch_up` semantics (inspired by Google Vertex AI Scheduler), result persistence and channel push.

## Capabilities

### New Capabilities
- `audit-logs`: Full-capture user operation audit trail with SaaS-ready storage architecture (AuditStore ABC, monthly partitioning, MinIO archival, SecurityPolicy rule engine, 6 modules × ~70 action types, REST query/export API)
- `scheduled-tasks`: Cron-based Agent/Workflow scheduling with multi-node distributed execution (APScheduler + PostgreSQL JobStore + advisory locks, schedule state machine, concurrency control)

### Modified Capabilities
- `rag-evaluation`: Wire evaluation engine to RAG pipeline for end-to-end answer generation; add `generated_answer` field to `EvaluationItemModel`
- `agent-evaluation`: Add `ToolCallAccuracyEvaluator` and `TaskCompletionEvaluator`; share `generated_answer` fix from rag-evaluation
- `evaluation-framework`: Fix `EvaluationEngine.run()` to use actual `generated_answer` instead of hardcoded empty string; support both manual-answer and pipeline-auto-answer modes

## Impact

- **Models**: New `AuditLogModel` (monthly-partitioned), `ScheduledTaskModel`, `ScheduledTaskExecutionModel`. Modified `EvaluationItemModel` (add `generated_answer` column).
- **API**: New routes under `/api/audit-logs` and `/api/schedules`. Modified evaluation run endpoint to support pipeline integration.
- **Services**: New `AuditService`, `ScheduledTaskService`. Modified `EvaluationEngine`, `EvaluationDatasetService`.
- **Middleware**: New `AuditMiddleware` (FastAPI BaseHTTPMiddleware) for automatic API operation capture.
- **Dependencies**: `apscheduler~=3.10` (new, optional `[scheduling]` group). `pg_partman` extension for partition management.
- **Database**: New Alembic migration for 3 new tables + 1 column addition + partition setup.
- **Infrastructure**: MinIO integration for cold audit log archival.
