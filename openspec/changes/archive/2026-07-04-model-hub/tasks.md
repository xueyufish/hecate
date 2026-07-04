## 1. Model Catalog Service (model-catalog spec)

- [x] 1.1 Create `src/hecate/model_hub/__init__.py` with public exports
- [x] 1.2 Create `src/hecate/model_hub/catalog_service.py` with CatalogService — aggregates ModelRegistryModel + ModelProviderModel + ModelPricingModel, computes effective_pricing and capability_badges
- [x] 1.3 Implement `list_models()` with filters (provider, capability, model_type, min_context, max_cost) and pagination
- [x] 1.4 Implement `get_model(model_id)` returning detailed entry with pricing history
- [x] 1.5 Implement `search_models(capabilities)` filtering by capabilities JSON
- [x] 1.6 Implement `compare_models(model_ids)` returning side-by-side comparison matrix

## 2. Model Catalog API (model-catalog spec)

- [x] 2.1 Create `src/hecate/api/management/model_catalog.py` — REST endpoints: GET /api/models/catalog (list+filter+paginate), GET /api/models/catalog/{model_id} (detail), GET /api/models/catalog/compare (comparison)
- [x] 2.2 Register model catalog router in `src/hecate/main.py`
- [x] 2.3 Create `tests/test_model_hub/test_catalog_service.py` — test aggregation, filtering, search, comparison

## 3. Model Deployment Model (model-lifecycle spec)

- [x] 3.1 Create `src/hecate/models/model_deployment.py` with ModelDeploymentModel(BaseModel) — model_id, channel (dev/staging/prod), version, deployment_config, approval_status, approved_by, approved_at, deprecated_at, sunset_at, workspace_id
- [x] 3.2 Create Pydantic schemas: ModelDeploymentCreateSchema, ModelDeploymentReadSchema, PromotionRequestSchema
- [x] 3.3 Create Alembic migration for model_deployments table
- [x] 3.4 Add unique constraint on (model_id, channel, deleted, deleted_at) to prevent duplicate deployments

## 4. Lifecycle Service (model-lifecycle spec)

- [x] 4.1 Create `src/hecate/model_hub/lifecycle_service.py` with LifecycleService — promotion, approval, deprecation, rollback
- [x] 4.2 Implement `promote(model_id, from_channel, to_channel)` — creates pending deployment in target channel
- [x] 4.3 Implement `approve(deployment_id, approver_id)` — sets approval_status=approved, records approver
- [x] 4.4 Implement `reject(deployment_id, reason)` — sets approval_status=rejected
- [x] 4.5 Implement `deprecate(model_id, sunset_at)` — sets deprecated_at and sunset_at on prod deployment
- [x] 4.6 Implement `cancel_deprecation(model_id)` — clears deprecated_at and sunset_at
- [x] 4.7 Implement `rollback(model_id, to_version)` — creates new deployment pointing to previous version
- [x] 4.8 Implement sunset check — scheduled task that disables deployments past sunset_at and triggers AlertService notifications at 30/7/1 day intervals

## 5. Lifecycle API (model-lifecycle spec)

- [x] 5.1 Create `src/hecate/api/management/model_lifecycle.py` — REST endpoints: POST /api/models/{id}/promote, POST /api/models/{id}/promote/{deployment_id}/approve, POST /api/models/{id}/promote/{deployment_id}/reject, POST /api/models/{id}/deprecate, POST /api/models/{id}/deprecate/cancel, GET /api/models/deployments, POST /api/models/{id}/rollback
- [x] 5.2 Register lifecycle router in `src/hecate/main.py`
- [x] 5.3 Create `tests/test_model_hub/test_lifecycle_service.py` — test promotion, approval, deprecation, rollback

## 6. Cache Strategy ABC + InMemory (intelligent-router spec)

- [x] 6.1 Create `src/hecate/model_hub/cache.py` with CacheStrategyABC — get, set, invalidate, stats abstract methods
- [x] 6.2 Implement InMemoryCacheStrategy(CacheStrategyABC) — dict with TTL-based expiry, pattern invalidation, stats tracking
- [x] 6.3 Implement `generate_cache_key(model, messages, temperature)` — SHA-256 hash with model prefix
- [x] 6.4 Add router cache settings to config: ROUTER_CACHE_ENABLED (default True), ROUTER_CACHE_TTL (default 300), ROUTER_CACHE_REDIS_URL, ROUTER_CACHE_FALLBACK_TO_MEMORY (default True), ROUTER_COST_AWARE (default True)

## 7. Redis Cache Strategy (intelligent-router spec)

- [x] 7.1 Implement RedisCacheStrategy(CacheStrategyABC) — requires redis package, connects via redis_url, falls back to InMemoryCacheStrategy if Redis unavailable
- [x] 7.2 Add `redis` to `[observability]` optional dependency group in pyproject.toml (already used by alerting)

## 8. Router Cache + Cost-Aware Integration (intelligent-router spec)

- [x] 8.1 Create `src/hecate/model_hub/intelligent_router.py` — wraps existing ModelRouter, adds cache check before LLM call and cache store after
- [x] 8.2 Implement cost-aware routing — consult BudgetService before model selection, switch to COST strategy when budget < 20%
- [x] 8.3 Integrate intelligent router into LLMService — replace direct ModelRouter usage with IntelligentRouter when cache is enabled
- [x] 8.4 Implement cache stats endpoint: GET /api/router/cache/stats — returns hits, misses, size, hit_rate

## 9. Tests

- [x] 9.1 Create `tests/test_model_hub/test_cache.py` — test InMemoryCacheStrategy (get/set/expire/invalidate/stats), cache key generation
- [x] 9.2 Create `tests/test_model_hub/test_intelligent_router.py` — test cache hit/miss integration, cost-aware routing behavior
- [x] 9.3 Create `tests/test_api/test_model_catalog_api.py` — test catalog list/filter/compare endpoints
- [x] 9.4 Create `tests/test_api/test_model_lifecycle_api.py` — test promote/approve/deprecate/rollback endpoints

## 10. Integration and Verification

- [x] 10.1 Update `src/hecate/plugin/spi/__init__.py` if needed for any new ABCs
- [x] 10.2 Run full verification: `ruff check src/hecate/ tests/ && ruff format --check src/ tests/ && mypy src/ && python -m pytest tests/ -q`
- [x] 10.3 Fix any lint, type, or test failures
