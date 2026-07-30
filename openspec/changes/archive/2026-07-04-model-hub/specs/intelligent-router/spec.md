## ADDED Requirements — 新增需求

### Requirement: CacheStrategyABC 抽象接口 — CacheStrategyABC abstract interface
系统应在 `model_hub/cache.py` 中定义 `CacheStrategyABC`，包含 `get(key: str) -> dict | None`、`set(key: str, value: dict, ttl: int) -> None`、`invalidate(pattern: str) -> int` 和 `stats() -> dict` 抽象方法。

#### Scenario: CacheStrategyABC 是抽象的 — CacheStrategyABC is abstract
- **WHEN** 代码尝试直接实例化 `CacheStrategyABC()`
- **THEN** 应抛出 `TypeError`

#### Scenario: 具体子类实现所有方法 — Concrete subclass implements all methods
- **WHEN** 子类实现了 `get`、`set`、`invalidate` 和 `stats`
- **THEN** 该子类应可实例化

### Requirement: InMemoryCacheStrategy 内置 — InMemoryCacheStrategy built-in
系统应定义 `InMemoryCacheStrategy(CacheStrategyABC)`，使用带 TTL 过期机制的字典。

#### Scenario: 缓存未命中 — Cache miss
- **WHEN** 调用 `get("nonexistent")`
- **THEN** 系统应返回 None

#### Scenario: TTL 内缓存命中 — Cache hit within TTL
- **WHEN** 调用 `set("key", {"response": "..."}, ttl=300)`，然后在 300 秒内调用 `get("key")`
- **THEN** 系统应返回缓存值

#### Scenario: TTL 后的缓存过期 — Cache expiry after TTL
- **WHEN** TTL 已过期
- **THEN** `get("key")` 应返回 None 并移除过期条目

#### Scenario: 模式失效 — Pattern invalidation
- **WHEN** 调用 `invalidate("gpt-4o:*")`
- **THEN** 匹配模式的所有键应被移除，并返回计数

#### Scenario: 缓存统计 — Cache stats
- **WHEN** 调用 `stats()`
- **THEN** 系统应返回 `{"hits": N, "misses": M, "size": K, "hit_rate": 0.XX}`

### Requirement: RedisCacheStrategy 可选 — RedisCacheStrategy optional
系统应定义 `RedisCacheStrategy(CacheStrategyABC)`，需要 `redis` 包和配置的 Redis URL。

#### Scenario: Redis 缓存初始化 — Redis cache initialization
- **WHEN** 使用 `redis_url="redis://localhost:6379/0"` 创建 RedisCacheStrategy
- **THEN** 该策略应连接到 Redis 并验证连接

#### Scenario: Redis 不可达回退 — Redis unavailable fallback
- **WHEN** Redis 不可达且 `ROUTER_CACHE_FALLBACK_TO_MEMORY=True`
- **THEN** 该策略应记录警告并回退到 InMemoryCacheStrategy

#### Scenario: Redis 未配置 — Redis not configured
- **WHEN** 未配置 Redis URL
- **THEN** 系统应默认使用 InMemoryCacheStrategy

### Requirement: 缓存键生成 — Cache key generation
系统应使用 SHA-256 哈希从模型调用参数生成确定性缓存键。

#### Scenario: 相同参数产生相同键 — Same parameters produce same key
- **WHEN** 使用相同输入两次调用 `generate_cache_key(model="gpt-4o", messages=[{"role": "user", "content": "Hello"}], temperature=0.7)`
- **THEN** 两次调用应返回相同的缓存键

#### Scenario: 不同温度产生不同键 — Different temperature produces different key
- **WHEN** 相同的消息使用 temperature=0.7 vs temperature=0.0
- **THEN** 缓存键应不同

#### Scenario: 键包含模型前缀 — Key includes model prefix
- **WHEN** 为模型 "gpt-4o" 生成缓存键
- **THEN** 键应以 "gpt-4o:" 为前缀，用于基于模式的失效

### Requirement: 路由器缓存集成 — Router cache integration
系统应将缓存集成到 LLM 调用路径中，通过 ModelRouter 在调用 LLM 前检查缓存，并在调用后存储响应。

#### Scenario: 缓存命中跳过 LLM 调用 — Cache hit skips LLM call
- **WHEN** 请求匹配缓存条目
- **THEN** 系统应返回缓存的响应，而不调用 LLM

#### Scenario: 缓存未命中调用 LLM 并存储结果 — Cache miss invokes LLM and stores result
- **WHEN** 请求不匹配任何缓存条目
- **THEN** 系统应调用 LLM，将响应以配置的 TTL 存储到缓存中，并返回响应

#### Scenario: 通过配置禁用缓存 — Cache disabled by config
- **WHEN** `ROUTER_CACHE_ENABLED=False`
- **THEN** 系统应跳过所有缓存查找，始终调用 LLM

### Requirement: 成本感知路由 — Cost-aware routing
系统应扩展 ModelRouter，在选择模型前可选地咨询 BudgetService，在剩余预算低时路由到更便宜的模型。

#### Scenario: 预算健康使用正常策略 — Budget healthy uses normal strategy
- **WHEN** 工作区剩余预算高于限制的 50%
- **THEN** 路由器应使用配置的路由策略（例如 BALANCED）

#### Scenario: 预算低时切换到成本策略 — Budget low switches to cost strategy
- **WHEN** 工作区剩余预算降至限制的 20% 以下
- **THEN** 路由器应切换到 COST 策略，选择最便宜的可用模型

#### Scenario: 预算耗尽阻止昂贵模型 — Budget exhausted blocks expensive models
- **WHEN** 达到工作区预算硬限制
- **THEN** 路由器应拒绝请求，返回 HTTP 429 "Budget exceeded"

### Requirement: 缓存和路由器配置 — Cache and router configuration
系统应向 Settings 类添加路由器缓存设置。

#### Scenario: 启用缓存 — Enable cache
- **WHEN** Settings 包含 `ROUTER_CACHE_ENABLED=True`（默认）
- **THEN** 路由器应使用配置的缓存策略

#### Scenario: 缓存 TTL — Cache TTL
- **WHEN** Settings 包含 `ROUTER_CACHE_TTL=300`（默认 300 秒）
- **THEN** 缓存条目应在配置的 TTL 后过期

#### Scenario: Redis URL — Redis URL
- **WHEN** Settings 包含 `ROUTER_CACHE_REDIS_URL="redis://localhost:6379/0"`
- **THEN** 系统应使用 RedisCacheStrategy

#### Scenario: 成本感知路由开关 — Cost-aware routing toggle
- **WHEN** Settings 包含 `ROUTER_COST_AWARE=True`（默认）
- **THEN** 路由器应咨询 BudgetService 进行成本感知的模型选择
