## ADDED Requirements

### Requirement: CacheStrategyABC abstract interface
The system SHALL define `CacheStrategyABC` in `model_hub/cache.py` with `get(key: str) -> dict | None`, `set(key: str, value: dict, ttl: int) -> None`, `invalidate(pattern: str) -> int`, and `stats() -> dict` abstract methods.

#### Scenario: CacheStrategyABC is abstract
- **WHEN** code attempts to instantiate `CacheStrategyABC()` directly
- **THEN** a `TypeError` SHALL be raised

#### Scenario: Concrete subclass implements all methods
- **WHEN** a subclass implements `get`, `set`, `invalidate`, and `stats`
- **THEN** the subclass SHALL be instantiable

### Requirement: InMemoryCacheStrategy built-in
The system SHALL define `InMemoryCacheStrategy(CacheStrategyABC)` using a dict with TTL-based expiry.

#### Scenario: Cache miss
- **WHEN** `get("nonexistent")` is called
- **THEN** the system SHALL return None

#### Scenario: Cache hit within TTL
- **WHEN** `set("key", {"response": "..."}, ttl=300)` is called, then `get("key")` within 300 seconds
- **THEN** the system SHALL return the cached value

#### Scenario: Cache expiry after TTL
- **WHEN** the TTL has expired
- **THEN** `get("key")` SHALL return None and remove the expired entry

#### Scenario: Pattern invalidation
- **WHEN** `invalidate("gpt-4o:*")` is called
- **THEN** all keys matching the pattern SHALL be removed and the count returned

#### Scenario: Cache stats
- **WHEN** `stats()` is called
- **THEN** the system SHALL return `{"hits": N, "misses": M, "size": K, "hit_rate": 0.XX}`

### Requirement: RedisCacheStrategy optional
The system SHALL define `RedisCacheStrategy(CacheStrategyABC)` that requires `redis` package and a configured Redis URL.

#### Scenario: Redis cache initialization
- **WHEN** RedisCacheStrategy is created with `redis_url="redis://localhost:6379/0"`
- **THEN** the strategy SHALL connect to Redis and verify connectivity

#### Scenario: Redis unavailable fallback
- **WHEN** Redis is unreachable and `ROUTER_CACHE_FALLBACK_TO_MEMORY=True`
- **THEN** the strategy SHALL log a warning and fall back to InMemoryCacheStrategy

#### Scenario: Redis not configured
- **WHEN** no Redis URL is configured
- **THEN** the system SHALL use InMemoryCacheStrategy as default

### Requirement: Cache key generation
The system SHALL generate deterministic cache keys from model invocation parameters using SHA-256 hash.

#### Scenario: Same parameters produce same key
- **WHEN** `generate_cache_key(model="gpt-4o", messages=[{"role": "user", "content": "Hello"}], temperature=0.7)` is called twice with identical inputs
- **THEN** both calls SHALL return the same cache key

#### Scenario: Different temperature produces different key
- **WHEN** the same messages are used with temperature=0.7 vs temperature=0.0
- **THEN** the cache keys SHALL differ

#### Scenario: Key includes model prefix
- **WHEN** a cache key is generated for model "gpt-4o"
- **THEN** the key SHALL be prefixed with "gpt-4o:" for pattern-based invalidation

### Requirement: Router cache integration
The system SHALL integrate caching into the LLM invocation path via ModelRouter, checking cache before calling the LLM and storing responses after.

#### Scenario: Cache hit skips LLM call
- **WHEN** a request matches a cached entry
- **THEN** the system SHALL return the cached response without invoking the LLM

#### Scenario: Cache miss invokes LLM and stores result
- **WHEN** a request does not match any cached entry
- **THEN** the system SHALL invoke the LLM, store the response in cache with configured TTL, and return the response

#### Scenario: Cache disabled by config
- **WHEN** `ROUTER_CACHE_ENABLED=False`
- **THEN** the system SHALL skip all cache lookups and always invoke the LLM

### Requirement: Cost-aware routing
The system SHALL extend ModelRouter to optionally consult BudgetService before selecting a model, routing to cheaper models when remaining budget is low.

#### Scenario: Budget healthy uses normal strategy
- **WHEN** remaining workspace budget is above 50% of limit
- **THEN** the router SHALL use the configured routing strategy (e.g., BALANCED)

#### Scenario: Budget low switches to cost strategy
- **WHEN** remaining workspace budget falls below 20% of limit
- **THEN** the router SHALL switch to COST strategy, selecting the cheapest capable model

#### Scenario: Budget exhausted blocks expensive models
- **WHEN** workspace budget hard limit is reached
- **THEN** the router SHALL reject the request with HTTP 429 "Budget exceeded"

### Requirement: Cache and router configuration
The system SHALL add router cache settings to the Settings class.

#### Scenario: Enable cache
- **WHEN** Settings includes `ROUTER_CACHE_ENABLED=True` (default)
- **THEN** the router SHALL use the configured cache strategy

#### Scenario: Cache TTL
- **WHEN** Settings includes `ROUTER_CACHE_TTL=300` (default 300 seconds)
- **THEN** cached entries SHALL expire after the configured TTL

#### Scenario: Redis URL
- **WHEN** Settings includes `ROUTER_CACHE_REDIS_URL="redis://localhost:6379/0"`
- **THEN** the system SHALL use RedisCacheStrategy

#### Scenario: Cost-aware routing toggle
- **WHEN** Settings includes `ROUTER_COST_AWARE=True` (default)
- **THEN** the router SHALL consult BudgetService for cost-aware model selection
