## Why

Hecate has multi-tenant RBAC (10.1, 10.2), tenant isolation (10.5), a cost dashboard (8.3), and alerting (8.6), but no way to enforce hard resource limits per tenant. A workspace can currently consume unlimited API calls, tokens, and dollars — only the in-memory rate limiter (60 RPM per API key) provides burst protection, and it's ephemeral with no persistence or cost/token awareness. Quota management closes the cost governance loop: Cost Dashboard shows spend, Alerting warns on thresholds, and Quota Management enforces hard limits that prevent overruns.

## What Changes

- **Add QuotaModel** — per-workspace or per-API-key quota definitions with resource type (requests, tokens, cost), limit value, window type (rolling 60s, daily, monthly), soft limit threshold, and enforcement mode (hard_reject or soft_allow).
- **Add QuotaUsageModel** — persistent usage tracking with period start/end, current used value, and last-updated timestamp. Supports periodic reset at window boundaries.
- **Add QuotaService** — CRUD for quota definitions, usage queries, quota check logic (is_exceeded), usage recording, and period reset management.
- **Add QuotaMiddleware** — FastAPI middleware that checks request-count quotas (RPM, daily requests) after auth context resolution but before route processing. Returns 429 with `Retry-After` and `X-Quota-*` response headers.
- **Add Post-LLM quota recording** — after each LLM call, record actual token usage and cost against applicable quotas. Trigger soft-limit alerts via the existing Alerting system (8.6) when thresholds are crossed.
- **Add QuotaEnforcement dependency** — FastAPI dependency that checks workspace-level quotas for resource-intensive endpoints (chat completions, knowledge upload).
- **Enhance existing RateLimiter** — upgrade `core/rate_limit.py` to support per-workspace RPM limits backed by the quota system, replacing the global 60 RPM default with configurable per-workspace limits.
- **Add API routers** — `/api/quotas` (CRUD for definitions), `/api/quotas/usage` (query current usage), `/api/quotas/reset` (manual period reset, admin only).
- **Add quota config settings** — default workspace RPM, default daily token limit, default monthly cost limit, enforcement toggle.

## Capabilities

### New Capabilities

- `quota-management`: Quota definition CRUD, usage tracking, enforcement middleware, post-LLM recording, and integration with Alerting for soft-limit notifications.

### Modified Capabilities

- `alerting`: Add quota_exceeded and quota_soft_limit_reached alert types that fire when usage crosses configured thresholds, reusing the existing AlertEvaluator and NotificationDispatcher infrastructure.

## Impact

- **New files**: `models/quota.py` (2 models + 3 enums + schemas), `services/quota_service.py`, `api/management/quotas.py`, `alembic/versions/xxxx_add_quota_tables.py`.
- **Modified files**: `api/middleware.py` (add QuotaMiddleware), `core/rate_limit.py` (integrate with quota system), `core/config.py` (quota settings), `engine/workers/llm_worker.py` (post-LLM quota recording), `main.py` (router + middleware registration), `tests/conftest.py` (module import).
- **Database**: 2 new tables (quotas, quota_usage) with composite indexes on (workspace_id, resource_type, window_type).
- **API**: 1 new router group under `/api/quotas` with CRUD + usage query + reset.
- **Middleware**: New quota check layer in the request pipeline, between auth and route handler.
