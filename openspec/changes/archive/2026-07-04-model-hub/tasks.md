## 1. 模型目录服务（model-catalog 规范）

- [x] 1.1 创建 `src/hecate/model_hub/__init__.py`，包含公共导出
- [x] 1.2 创建 `src/hecate/model_hub/catalog_service.py`，包含 CatalogService——聚合 ModelRegistryModel + ModelProviderModel + ModelPricingModel，计算 effective_pricing 和 capability_badges
- [x] 1.3 实现带过滤器的 `list_models()`（provider、capability、model_type、min_context、max_cost）和分页
- [x] 1.4 实现返回带定价历史详细条目的 `get_model(model_id)`
- [x] 1.5 实现按 capabilities JSON 过滤的 `search_models(capabilities)`
- [x] 1.6 实现返回并排比较矩阵的 `compare_models(model_ids)`

## 2. 模型目录 API（model-catalog 规范）

- [x] 2.1 创建 `src/hecate/api/management/model_catalog.py` — REST 端点：GET /api/models/catalog（列表+过滤+分页）、GET /api/models/catalog/{model_id}（详情）、GET /api/models/catalog/compare（比较）
- [x] 2.2 在 `src/hecate/main.py` 中注册模型目录路由器
- [x] 2.3 创建 `tests/test_model_hub/test_catalog_service.py` — 测试聚合、过滤、搜索、比较

## 3. 模型部署模型（model-lifecycle 规范）

- [x] 3.1 创建 `src/hecate/models/model_deployment.py`，包含 ModelDeploymentModel(BaseModel) — model_id、channel（dev/staging/prod）、version、deployment_config、approval_status、approved_by、approved_at、deprecated_at、sunset_at、workspace_id
- [x] 3.2 创建 Pydantic 模式：ModelDeploymentCreateSchema、ModelDeploymentReadSchema、PromotionRequestSchema
- [x] 3.3 为 model_deployments 表创建 Alembic 迁移
- [x] 3.4 添加 (model_id, channel, deleted, deleted_at) 的唯一约束以防止重复部署

## 4. 生命周期服务（model-lifecycle 规范）

- [x] 4.1 创建 `src/hecate/model_hub/lifecycle_service.py`，包含 LifecycleService — 提升、审批、弃用、回滚
- [x] 4.2 实现 `promote(model_id, from_channel, to_channel)` — 在目标通道中创建待审批部署
- [x] 4.3 实现 `approve(deployment_id, approver_id)` — 设置 approval_status=approved，记录审批人
- [x] 4.4 实现 `reject(deployment_id, reason)` — 设置 approval_status=rejected
- [x] 4.5 实现 `deprecate(model_id, sunset_at)` — 在 prod 部署上设置 deprecated_at 和 sunset_at
- [x] 4.6 实现 `cancel_deprecation(model_id)` — 清除 deprecated_at 和 sunset_at
- [x] 4.7 实现 `rollback(model_id, to_version)` — 创建指向之前版本的新部署
- [x] 4.8 实现日落检查——计划任务，禁用超过 sunset_at 的部署，并按照 30/7/1 天间隔触发 AlertService 通知

## 5. 生命周期 API（model-lifecycle 规范）

- [x] 5.1 创建 `src/hecate/api/management/model_lifecycle.py` — REST 端点：POST /api/models/{id}/promote、POST /api/models/{id}/promote/{deployment_id}/approve、POST /api/models/{id}/promote/{deployment_id}/reject、POST /api/models/{id}/deprecate、POST /api/models/{id}/deprecate/cancel、GET /api/models/deployments、POST /api/models/{id}/rollback
- [x] 5.2 在 `src/hecate/main.py` 中注册生命周期路由器
- [x] 5.3 创建 `tests/test_model_hub/test_lifecycle_service.py` — 测试提升、审批、弃用、回滚

## 6. 缓存策略 ABC + InMemory（intelligent-router 规范）

- [x] 6.1 创建 `src/hecate/model_hub/cache.py`，包含 CacheStrategyABC — get、set、invalidate、stats 抽象方法
- [x] 6.2 实现 InMemoryCacheStrategy(CacheStrategyABC) — 带 TTL 过期、模式失效、统计跟踪的字典
- [x] 6.3 实现 `generate_cache_key(model, messages, temperature)` — 带模型前缀的 SHA-256 哈希
- [x] 6.4 向配置添加路由器缓存设置：ROUTER_CACHE_ENABLED（默认 True）、ROUTER_CACHE_TTL（默认 300）、ROUTER_CACHE_REDIS_URL、ROUTER_CACHE_FALLBACK_TO_MEMORY（默认 True）、ROUTER_COST_AWARE（默认 True）

## 7. Redis 缓存策略（intelligent-router 规范）

- [x] 7.1 实现 RedisCacheStrategy(CacheStrategyABC) — 需要 redis 包，通过 redis_url 连接，如果 Redis 不可用则回退到 InMemoryCacheStrategy
- [x] 7.2 在 pyproject.toml 的 `[observability]` 可选依赖组中添加 `redis`（已用于告警）

## 8. 路由器缓存 + 成本感知集成（intelligent-router 规范）

- [x] 8.1 创建 `src/hecate/model_hub/intelligent_router.py` — 包装现有的 ModelRouter，在 LLM 调用前添加缓存检查，调用后添加缓存存储
- [x] 8.2 实现成本感知路由——在模型选择前咨询 BudgetService，预算 < 20% 时切换到 COST 策略
- [x] 8.3 将智能路由器集成到 LLMService — 当缓存启用时，用 IntelligentRouter 替换直接的 ModelRouter 使用
- [x] 8.4 实现缓存统计端点：GET /api/router/cache/stats — 返回 hits、misses、size、hit_rate

## 9. 测试

- [x] 9.1 创建 `tests/test_model_hub/test_cache.py` — 测试 InMemoryCacheStrategy（get/set/expire/invalidate/stats）、缓存键生成
- [x] 9.2 创建 `tests/test_model_hub/test_intelligent_router.py` — 测试缓存命中/未命中集成、成本感知路由行为
- [x] 9.3 创建 `tests/test_api/test_model_catalog_api.py` — 测试目录列表/过滤/比较端点
- [x] 9.4 创建 `tests/test_api/test_model_lifecycle_api.py` — 测试提升/审批/弃用/回滚端点

## 10. 集成和验证

- [x] 10.1 如有需要更新 `src/hecate/plugin/spi/__init__.py` 以导出新的 ABC
- [x] 10.2 运行完整验证：`ruff check src/hecate/ tests/ && ruff format --check src/ tests/ && mypy src/ && python -m pytest tests/ -q`
- [x] 10.3 修复任何 lint、类型或测试失败
