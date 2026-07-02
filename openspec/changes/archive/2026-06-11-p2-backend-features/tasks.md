## 1. Evaluation Framework Fix

- [x] 1.1 Add `generated_answer` column to `EvaluationItemModel` (nullable TEXT, default NULL) and update Pydantic schemas (`EvaluationItemCreateSchema`, `EvaluationItemReadSchema`)
- [ ] 1.2 Create Alembic migration: add `generated_answer` column to `evaluation_items` table *(blocked: needs running PostgreSQL)*
- [x] 1.3 Add `answer_source` field to `EvaluationRunCreateSchema` (enum: manual, pipeline, auto; default auto) and `AnswerSource` enum in `types.py`
- [x] 1.4 Refactor `EvaluationEngine.run()` to accept `answer_source` parameter and use item's `generated_answer` when available instead of hardcoded empty string
- [x] 1.5 Implement pipeline answer generation in `EvaluationEngine` — for RAG evaluations, invoke `KnowledgeBaseService.search()` with item query, use retrieved contexts to generate answer via LLM service
- [x] 1.6 Update evaluation run API endpoint to accept and pass `answer_source` parameter
- [x] 1.7 Write/update tests: `EvaluationEngine` with manual answers, pipeline mode, auto fallback

## 2. Agent Evaluators (New)

- [x] 2.1 Implement `ToolCallAccuracyEvaluator` in `services/evaluation/agent_evaluators.py` — LLM-as-Judge comparing actual vs expected tool calls, scoring selection + parameter accuracy
- [x] 2.2 Add `TOOL_CALL_ACCURACY_PROMPT` to `services/evaluation/prompts.py`
- [x] 2.3 Implement `TaskCompletionEvaluator` in `services/evaluation/agent_evaluators.py` — LLM-as-Judge assessing task completion from query + response
- [x] 2.4 Add `TASK_COMPLETION_PROMPT` to `services/evaluation/prompts.py`
- [x] 2.5 Register both new evaluators in `_EVALUATOR_REGISTRY` in `api/evaluation.py`
- [x] 2.6 Write tests for `ToolCallAccuracyEvaluator`: correct tools, wrong tools, no tool calls
- [x] 2.7 Write tests for `TaskCompletionEvaluator`: full completion, partial, not attempted

## 3. Audit Logs — Models & Store

- [x] 3.1 Define `AuditAction` enum (6 modules × actions: AUTH, AGENT, WORKFLOW, KNOWLEDGE, TOOL, SYSTEM) in new file `models/audit.py`
- [x] 3.2 Create `AuditLogModel` in `models/audit.py` — partitioned table with all spec fields (org_id, workspace_id, user_id, action, resource_type, resource_id, request_method, request_path, response_status, ip_address, user_agent, success, error_code, metadata JSONB)
- [x] 3.3 Create Pydantic schemas: `AuditLogReadSchema`, `AuditLogQuerySchema`, `AuditLogExportSchema`
- [x] 3.4 Define `AuditStore` ABC in new file `services/audit/store.py` with methods: `write()`, `query()`, `export()`, `archive()`
- [x] 3.5 Implement `DatabaseAuditStore` — writes to partitioned PostgreSQL, query with filters, CSV/JSON export
- [ ] 3.6 Create Alembic migration: install `pg_partman` extension, create partitioned `audit_logs` table, configure pg_partman for monthly auto-creation *(blocked: needs running PostgreSQL)*

## 4. Audit Logs — Middleware & Writer

- [x] 4.1 Implement `AuditMiddleware` (BaseHTTPMiddleware) in new file `api/middleware.py` — extract AuthContext, record method/path/status/timing, enqueue audit event
- [x] 4.2 Implement async batch writer (`AuditBatchWriter`) using `asyncio.Queue` — drain queue in batches of N, insert via `DatabaseAuditStore`
- [x] 4.3 Register `AuditMiddleware` in `main.py` (before CORS middleware for proper ordering)
- [x] 4.4 Start/stop `AuditBatchWriter` in application lifespan (`main.py`)
- [x] 4.5 Write tests for `AuditMiddleware`: authenticated request, unauthenticated request, excluded paths, error responses

## 5. Audit Logs — Security Policy Engine

- [x] 5.1 Define `AuditSecurityPolicy` dataclass and `PolicyAction` enum (ALERT, BLOCK, RATE_LIMIT) in new file `services/audit/policy.py`
- [x] 5.2 Implement `PolicyEngine` — evaluates audit events against registered policies using time-window counting
- [x] 5.3 Implement 3 built-in policies: `bulk_delete_protection`, `off_hours_sensitive_ops`, `unusual_ip_detection`
- [x] 5.4 Integrate `PolicyEngine` into `AuditBatchWriter` — evaluate events before batch insert, log violations
- [x] 5.5 Write tests for each policy: trigger conditions, no-trigger conditions

## 6. Audit Logs — API & Archival

- [x] 6.1 Create `AuditService` in new file `services/audit/service.py` — wraps `AuditStore` for business logic (query with tenant filtering, export, stats)
- [x] 6.2 Create API routes in new file `api/audit.py`: `GET /api/audit-logs` (paginated query), `GET /api/audit-logs/export` (CSV/JSON export), `GET /api/audit-logs/stats` (aggregation)
- [x] 6.3 Register audit router in `main.py`
- [x] 6.4 Implement MinIO archival worker — drop old partitions, export to MinIO as compressed JSON
- [x] 6.5 Add retention configuration to `core/config.py` (`AUDIT_RETENTION_DAYS`, `AUDIT_ARCHIVAL_ENABLED`, `AUDIT_MINIO_BUCKET`)
- [x] 6.6 Write integration tests: full audit flow (request → middleware → queue → DB → query)

## 7. Scheduled Tasks — Models & Scheduler

- [x] 7.1 Create `ScheduledTaskModel` in new file `models/scheduled_task.py` — all spec fields (cron_expression, agent_id, workflow_id, execution_config JSONB, state, max_concurrent_runs, catch_up, timezone, next_run_at, last_run_at)
- [x] 7.2 Create `ScheduledTaskExecutionModel` in same file — task_id FK, started_at, completed_at, status, result_summary JSONB, error_message, duration_ms, triggered_by
- [x] 7.3 Create Pydantic schemas for both models (Create, Update, Read)
- [x] 7.4 Add `apscheduler~=3.10` to `[scheduling]` optional dependency group in `pyproject.toml`
- [x] 7.5 Implement `ScheduleManager` in new file `services/scheduling/manager.py` — wraps APScheduler with PostgreSQL JobStore, manages schedule CRUD, cron expression parsing and validation
- [ ] 7.6 Create Alembic migration: `scheduled_tasks` and `scheduled_task_executions` tables *(blocked: needs running PostgreSQL)*

## 8. Scheduled Tasks — Execution & Multi-Node

- [x] 8.1 Implement advisory lock acquisition in `ScheduleManager._execute_with_lock()` — `pg_try_advisory_lock(hashint8(task_id, scheduled_time))`, release on completion
- [x] 8.2 Implement execution binding — `AgentExecutor` and `WorkflowExecutor` that create conversation/session, invoke agent or workflow, capture result summary
- [x] 8.3 Implement `max_concurrent_runs` enforcement — check active executions before triggering new one, skip if limit reached
- [x] 8.4 Implement `catch_up` logic — when scheduler resumes after pause, queue missed executions if `catch_up=true`
- [x] 8.5 Integrate `ScheduleManager` into app lifespan — start scheduler on startup, stop gracefully on shutdown
- [x] 8.6 Write tests: cron validation, state machine transitions, advisory lock contention

## 9. Scheduled Tasks — API

- [x] 9.1 Create `ScheduledTaskService` in new file `services/scheduling/service.py` — CRUD + state transitions + manual trigger
- [x] 9.2 Create API routes in new file `api/schedules.py`: POST (create), GET (list), GET/{id}, PUT/{id}, DELETE/{id}, POST/{id}/trigger, PUT/{id}/pause, PUT/{id}/resume, GET/{id}/executions
- [x] 9.3 Register schedules router in `main.py`
- [x] 9.4 Write tests: create schedule, pause/resume, manual trigger, execution history query

## 10. Verification

- [x] 10.1 Run `ruff check src/hecate/ tests/` — zero errors
- [x] 10.2 Run `ruff format --check src/ tests/` — zero errors
- [x] 10.3 Run `mypy src/` — zero errors
- [x] 10.4 Run `python -m pytest tests/ -q` — all tests pass (no new failures)
- [x] 10.5 Update `docs/features/feature-catalog.md` — mark 7.1, 7.2, 8.7, 13.9 as ✅
- [x] 10.6 Update `docs/features/roadmap.md` — update P2 stats to 53/57 (93%)
