## Why

Hecate's existing model management is provider-centric: ModelProviderModel stores connection credentials and ModelRegistryModel lists available models, but there is no unified catalog for users to browse, compare, and select models by capability. The Model Lifecycle Manager (6.45) adds staging channels (dev/staging/prod) with promotion workflows so operators can safely roll out model changes. The Intelligent Router (6.14) adds request-level routing with caching to optimize cost and latency. Together these three features transform Hecate from "model connection manager" to "model hub" — the central place where operators govern the entire model fleet.

## What Changes

- **Model Catalog (6.44)**: Build a browseable, searchable model catalog API that aggregates ModelRegistryModel entries with pricing data from ModelPricingModel. Add capability badges (vision, function-calling, streaming, context-length), provider comparison matrix, and one-click enablement workflow. The catalog enriches existing model data with metadata tags, category grouping, and search/filter capabilities.
- **Model Lifecycle Manager (6.45)**: Add staging channels (dev/staging/prod) to ModelRegistryModel. Implement promotion workflows with approval gates — models move from dev → staging → prod with audit trail. Add deprecation scheduling with automated sunset notifications and rollback support. Introduce ModelVersionModel to track versioned model configurations.
- **Intelligent Router with Caching (6.14)**: Build on the existing RoutingStrategy to add semantic caching (similar prompts return cached responses), cost-aware routing (route to cheaper models when budget is low), and latency-aware fallback. Add CacheStrategy ABC with InMemoryCache and RedisCache implementations.

## Capabilities

### New Capabilities

- `model-catalog`: Browseable/searchable model catalog with capability badges, provider comparison matrix, category grouping, and search/filter. Aggregates ModelRegistryModel + ModelPricingModel data into a unified catalog view. REST API for listing, filtering, comparing, and enabling models.
- `model-lifecycle`: Versioned model registry with staging channels (dev/staging/prod), promotion workflows with approval gates, deprecation scheduling with sunset notifications, and rollback support. Extends ModelRegistryModel with channel and version tracking.
- `intelligent-router`: Semantic caching with TTL, cost-aware routing, latency-aware fallback, and per-agent model override. CacheStrategy ABC with InMemoryCache and RedisCache implementations. Integrates with existing RoutingStrategy and BudgetService.

### Modified Capabilities

_(none — all new capabilities build on existing ModelProviderModel, ModelRegistryModel, ModelPricingModel, LLMService, and RoutingStrategy without changing their spec-level behavior)_

## Impact

- **New modules**: `src/hecate/model_hub/` (catalog service, lifecycle service, intelligent router), `src/hecate/models/model_version.py` (ModelVersionModel), `src/hecate/models/model_deployment.py` (ModelDeploymentModel for staging channel tracking)
- **Existing files modified**: `src/hecate/models/model_provider.py` (add `channel` and `version` fields to ModelRegistryModel), `src/hecate/services/llm/routing.py` (integrate caching), `src/hecate/main.py` (register model hub router)
- **New API endpoints**: `/api/models/catalog`, `/api/models/{id}/promote`, `/api/models/{id}/deprecate`, `/api/models/deployments`, `/api/router/cache/stats`, `/api/router/strategy`
- **Database migrations**: ModelVersionModel table, ModelDeploymentModel table, ModelRegistryModel channel/version columns
- **New dependencies**: `redis` (optional, for RedisCache), no other new packages
