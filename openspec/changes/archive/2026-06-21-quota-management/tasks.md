## 1. Models Layer

- [x] 1.1 Create `src/hecate/models/quota.py` with `QuotaResourceType` enum (requests, tokens, cost), `QuotaScope` enum (workspace, api_key), `QuotaWindowType` enum (rolling_minute, daily, monthly), `EnforcementMode` enum (hard_reject, soft_allow)
- [x] 1.2 Implement `QuotaModel(BaseModel)` with fields: name, resource_type, scope, scope_id, limit_value, soft_limit (nullable), window_type, enforcement, enabled, workspace_id. Index on (workspace_id, scope, resource_type)
- [x] 1.3 Implement `QuotaUsageModel(BaseModel)` with fields: quota_id (UUID FK), period_start, period_end, used_value, last_updated, workspace_id. Index on (quota_id, period_start)
- [x] 1.4 Implement Pydantic schemas: QuotaCreateSchema, QuotaUpdateSchema, QuotaReadSchema, QuotaUsageReadSchema (with computed remaining and utilization_pct fields)

## 2. Migration

- [x] 2.1 Create Alembic migration creating 2 tables (quotas, quota_usage) with indexes on workspace_id, quota_id, period_start. Chain from current head (f7a8b9c0d1e2)
- [x] 2.2 Verify migration applies cleanly and tables match ORM definitions

## 3. Quota Service

- [x] 3.1 Create `src/hecate/services/quota_service.py` with `QuotaService` class (inject AsyncSession, workspace_id)
- [x] 3.2 Implement quota CRUD: create_quota, list_quotas (filter by resource_type, scope), get_quota, update_quota, delete_quota (soft delete)
- [x] 3.3 Implement usage queries: get_usage (returns current period usage for a quota), list_usage (all quotas for workspace with remaining/utilization)
- [x] 3.4 Implement `check_quota(resource_type, scope, scope_id, window_type)` — returns (allowed, remaining, reset_at). Queries QuotaUsageModel for current period, creates new period record if expired (auto-reset)
- [x] 3.5 Implement `record_usage(resource_type, scope, scope_id, window_type, amount)` — atomically increments used_value for current period. Triggers soft-limit alert via AlertService when threshold crossed for first time in period
- [x] 3.6 Implement `reset_quota(quota_id)` — sets current period used_value to 0
- [x] 3.7 Implement quota definition caching: in-memory cache with 60s TTL, keyed by workspace_id. `get_active_quotas(workspace_id)` returns cached or reloads

## 4. Quota Middleware

- [x] 4.1 Create `src/hecate/core/quota_middleware.py` with `QuotaMiddleware` class (Starlette BaseHTTPMiddleware)
- [x] 4.2 Implement request-count pre-check: resolve AuthContext from request, get workspace_id and API key, check workspace-level daily request quotas and API-key-level RPM quotas. Return 429 with Retry-After and X-Quota-* headers if exceeded
- [x] 4.3 Implement response header injection: on successful responses, add X-Quota-Limit-Requests, X-Quota-Remaining-Requests, X-Quota-Reset-Requests headers from cached quota data
- [x] 4.4 Skip middleware for excluded paths (/health, /docs, /openapi.json, /redoc, /metrics)
- [x] 4.5 Register QuotaMiddleware in main.py middleware stack (after CORS, before routes)

## 5. Post-LLM Quota Recording

- [x] 5.1 Add quota recording hook to LLMWorker: after LLM call completes and usage is recorded in TraceModel, call QuotaService.record_usage for tokens (daily + monthly) and cost (monthly)
- [x] 5.2 Integrate soft-limit alerting: when record_usage crosses soft_limit threshold for first time in period, create AlertEventModel via AlertService with alert_type quota_soft_limit_reached
- [x] 5.3 Add quota pre-check to LLM-invoking endpoints (chat completions, agent execution): before processing, check if workspace has exceeded hard-limit token or cost quotas. If exceeded, return 429

## 6. Alerting Integration

- [x] 6.1 Add QUOTA_SOFT_LIMIT_REACHED to AlertType enum in models/alert.py
- [x] 6.2 Add QuotaSoftLimitSignalProvider to signal_provider.py (reads QuotaUsageModel utilization)
- [x] 6.3 Update NotificationDispatcher message templates to include quota name and utilization percentage in alert messages

## 7. API Layer

- [x] 7.1 Create `src/hecate/api/management/quotas.py` with quotas_router
- [x] 7.2 Implement POST/GET/PUT/DELETE `/api/quotas` for quota definition CRUD with AuthContext + AsyncSession dependencies
- [x] 7.3 Implement GET `/api/quotas/usage` with optional resource_type filter, returning usage summaries with remaining and utilization_pct
- [x] 7.4 Implement POST `/api/quotas/{id}/reset` with workspace admin role check
- [x] 7.5 Register quotas_router in main.py with prefix /api, tags ["quotas"]

## 8. Configuration

- [x] 8.1 Add quota settings to core/config.py: QUOTA_ENABLED (bool, default True), QUOTA_DEFAULT_WORKSPACE_RPM (int, default 60), QUOTA_CACHE_TTL (int, default 60)

## 9. Tests

- [x] 9.1 Create `tests/test_services/test_quota_service.py` — test QuotaModel/QuotaUsageModel creation, quota CRUD, usage queries, check_quota logic (allowed/denied), record_usage (increment + auto-reset), reset_quota
- [x] 9.2 Test quota definition caching: cache hit avoids DB query, cache refresh after TTL
- [x] 9.3 Test period auto-reset: expired daily period creates new record, expired monthly period creates new record
- [x] 9.4 Test soft-limit alerting: crossing soft_limit creates AlertEventModel, crossing twice in same period creates only one event
- [x] 9.5 Test middleware: request allowed when under quota, 429 when exceeded, headers present, no headers when no quota configured
- [x] 9.6 Test post-LLM recording: token usage increments daily+monthly quotas, cost increments monthly quota

## 10. Verification

- [x] 10.1 Run ruff check src/hecate/ tests/ — zero errors
- [x] 10.2 Run ruff format --check src/ tests/ — zero changes needed
- [x] 10.3 Run mypy src/ — zero errors
- [x] 10.4 Run pytest tests/ -q — all tests pass
