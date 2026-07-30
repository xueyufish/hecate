## Why — 动机

当 LLM 提供商（例如 OpenAI）经历部分或完全故障时，每个传入的请求仍首先尝试调用主模型，等待超时，然后回退。这会产生惊群效应：所有请求在到达可工作的回退模型之前都要承受超时惩罚。每个路由前缀的熔断器可以立即隔离故障前缀，直接跳转到健康的替代方案。

## What Changes — 变更内容

- 在 `services/llm/` 中添加一个 `CircuitBreakerManager`，维护每个前缀的熔断器（key 为从 LiteLLM 模型名称中提取的路由前缀，例如从 `"openai/gpt-4o"` 中提取 `"openai"`）。
- 复用 `services/validation/retry_policy.py` 中的现有 `CircuitBreaker` / `CircuitState` 作为每个前缀的熔断器实例。
- 将 `CircuitBreakerManager` 集成到 `LLMService` 中：在调用 LiteLLM 前检查熔断器状态，每次调用后记录成功/失败。
- 在 HALF_OPEN 状态下，允许单个探测请求（使用 `asyncio.Lock`）测试故障前缀；所有其他请求跳过到回退。
- 过滤回退链，跳过其前缀熔断器为 OPEN 的模型。
- 在 `CircuitBreakerManager` 上预留 `on_state_change` 回调钩子，用于将来的 EventStore 集成（P3）。
- 在 `feature-catalog.md` 中添加 P3 特性条目（15.6）以跟踪 EventStore 集成。

## Capabilities — 能力变更

### 新增能力

- `llm-circuit-breaker`: 每个前缀的 LLM 调用熔断器——状态机（CLOSED → OPEN → HALF_OPEN）、单探测恢复、回退链过滤、可选的状态变更回调钩子。

### 修改的能力

- 无。`LLMService` 获得一个可选依赖；其公开 API 没有破坏性变更。

## Impact — 影响范围

- **代码**: `src/hecate/services/llm/service.py`（集成熔断器），新文件 `src/hecate/services/llm/circuit_breaker.py`（管理器），`src/hecate/services/validation/retry_policy.py`（无变更，原样复用）。
- **测试**: 新文件 `tests/test_services/test_llm/test_circuit_breaker.py`，更新现有的 LLM 服务测试以覆盖熔断器集成。
- **依赖**: 无新的外部包。复用 `validation/retry_policy.py` 中的现有 `CircuitBreaker`。
- **API**: 无 API 变更。熔断器是对调用方透明的内部优化。
- **功能目录**: 向 `docs/features/feature-catalog.md` 添加 P3 条目 15.6（熔断器 EventStore 集成）。