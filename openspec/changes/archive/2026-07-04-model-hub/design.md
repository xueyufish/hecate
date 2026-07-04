## Context

Hecate already has a solid model management foundation:

- **ModelProviderModel** — provider registry with encrypted API keys, base_url, config, status
- **ModelRegistryModel** — registered models linked to providers with capabilities (JSON), max_context, model_type
- **ModelPricingModel** — time-ranged pricing with workspace isolation
- **LLMService** — LiteLLM wrapper with streaming, tool calling, fallback models
- **ModelRouter** — 4 routing strategies (COST, LATENCY, CAPABILITY, BALANCED) with constraints
- **CircuitBreakerManager** — per-prefix circuit breaker (CLOSED → OPEN → HALF_OPEN)
- **CostService** — pricing CRUD, cost aggregation from TraceModel.usage

What's missing: a unified catalog view, lifecycle management (staging channels, promotion), and intelligent caching. The Model Hub adds these three capabilities on top of the existing infrastructure.

## Goals / Non-Goals

**Goals:**
- Build a browseable model catalog API that aggregates registry + pricing data with search, filter, and comparison
- Add staging channels (dev/staging/prod) to models with promotion workflows and audit trail
- Add semantic caching to the intelligent router for cost and latency optimization
- Add deprecation scheduling with automated sunset notifications
- Integrate with existing BudgetService for cost-aware routing

**Non-Goals:**
- Model fine-tuning pipeline (6.6 — separate feature, L effort)
- Self-hosted inference / managed model deployment (6.5 — separate feature, infrastructure-heavy)
- Model Management Console UI (O10+G4 — frontend, separate change)
- Multi-modal model classification (6.11 — separate feature)
- A/B testing framework for model comparison (future enhancement)
- Model performance benchmarking suite (future enhancement)

## Decisions

### Decision 1: Catalog as read-only aggregation layer, not new table

**Choice**: Model Catalog is a service-layer aggregation of ModelRegistryModel + ModelPricingModel + ModelProviderModel, not a separate database table.

**Rationale**: The data already exists across three tables. A catalog table would duplicate data and cause sync issues. Instead, CatalogService joins the three tables and enriches with computed fields (effective pricing, capability badges, provider status).

**Alternatives considered**:
- Separate CatalogModel table — duplicates data, sync complexity
- Materialized view — PostgreSQL-specific, not portable to SQLite/MySQL

### Decision 2: Staging channels via ModelDeploymentModel, not ModelRegistryModel column

**Choice**: Create a separate `ModelDeploymentModel` table to track staging channel assignments (dev/staging/prod) per model, rather than adding a `channel` column to ModelRegistryModel.

**Rationale**: A model can exist in multiple channels simultaneously (e.g., gpt-4o in prod for agents, gpt-4o-mini in dev for testing). A separate deployment table allows many-to-many relationships between models and channels with audit trail (who promoted, when, approval status).

**Alternatives considered**:
- Single `channel` column on ModelRegistryModel — limits one model to one channel
- ModelVersionModel with embedded channel — conflates versioning with deployment

### Decision 3: Semantic caching via hash-based CacheStrategy ABC

**Choice**: Define `CacheStrategyABC` in `model_hub/cache.py` with `get(key)`, `set(key, value, ttl)`, `invalidate(pattern)` abstract methods. Implement `InMemoryCacheStrategy` (dict with TTL) and `RedisCacheStrategy` (optional, requires redis).

**Rationale**: Follows the existing ABC pattern (AuthProviderABC, SecretProviderABC). Cache key is SHA-256 hash of (model + messages + temperature). Semantic similarity is a future enhancement — initial implementation uses exact hash matching.

**Alternatives considered**:
- GPTCache / semantic similarity cache — adds heavy dependency, premature for initial release
- LiteLLM built-in caching — limited configuration, no custom invalidation

### Decision 4: Promotion workflow with approval gates

**Choice**: Model promotion (dev → staging → prod) requires approval from workspace admin. `ModelDeploymentModel` tracks `approval_status` (pending/approved/rejected) and `approved_by`.

**Rationale**: Enterprise customers require controlled model rollouts. The approval gate prevents accidental production model changes. The audit trail (who approved, when) satisfies compliance requirements.

**Alternatives considered**:
- Automatic promotion (no approval) — risky for production environments
- External approval system (Jenkins, ArgoCD) — infrastructure complexity

### Decision 5: Deprecation scheduling with sunset date

**Choice**: Add `deprecated_at` and `sunset_at` fields to ModelDeploymentModel. When `sunset_at` passes, the model is automatically disabled.

**Rationale**: Gives operators a grace period to migrate agents to new models. The sunset date triggers AlertService notifications at 30/7/1 day intervals.

**Alternatives considered**:
- Immediate deprecation (no grace period) — breaks running agents
- Separate DeprecationScheduleModel — over-engineering for a simple date field

### Decision 6: Cost-aware routing via BudgetService integration

**Choice**: Extend ModelRouter to optionally consult BudgetService before selecting a model. If remaining budget is low, route to cheaper models.

**Rationale**: BudgetService already tracks spending against quotas. The router can check `budget_remaining` and switch to a cost-optimized strategy when budget is constrained.

**Alternatives considered**:
- Separate budget router — duplicates routing logic
- No budget integration — misses cost optimization opportunity

## Risks / Trade-offs

- **[Cache invalidation complexity]** → Use model_id + version as cache key prefix for targeted invalidation; TTL as safety net
- **[Catalog query performance]** → Add database indexes on model_id + provider_id; consider pagination defaults (50 per page)
- **[Promotion bottleneck]** → Allow workspace admins to self-approve in single-workspace mode; require org admin for multi-workspace
- **[Redis dependency]** → InMemoryCache as default; RedisCache only when configured; no crash if Redis unavailable
- **[Model deprecation breaking agents]** → Sunset notifications via AlertService 30/7/1 days before; agents fallback to default model on deprecation

## Migration Plan

1. **Phase 1: Catalog** — CatalogService read-only aggregation. No schema changes. New `/api/models/catalog` endpoints.
2. **Phase 2: Lifecycle** — ModelDeploymentModel table + migration. ModelRegistryModel unchanged. New deployment/promotion/deprecation endpoints.
3. **Phase 3: Caching** — CacheStrategyABC + InMemoryCache. Integrate into LLMService via ModelRouter. No schema changes.
4. **Phase 4: Cost-aware routing** — BudgetService integration into ModelRouter. No schema changes.

**Rollback**: Each phase is independent. Catalog endpoints can be unmounted. Deployment table can be dropped. Cache can be disabled via config flag.

## Open Questions

- Should the catalog support **model recommendation** (suggest best model for a task)? Initial: no, future enhancement.
- Should caching support **streaming responses**? Initial: no, cache only non-streaming completions. Streaming caching is complex (partial responses).
- Should promotion workflow support **canary deployment** (percentage-based traffic splitting)? Initial: no, future enhancement with ModelRouter traffic splitting.
