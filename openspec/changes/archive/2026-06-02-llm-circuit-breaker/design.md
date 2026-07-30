## Context — 背景

`LLMService`（`services/llm/service.py`）封装了 LiteLLM 并支持线性回退链：主模型失败 → 按顺序尝试每个 `fallback_models` 条目。请求之间没有记忆——如果 OpenAI 宕机，每个请求仍首先尝试 `openai/gpt-4o`，等待超时，然后回退。这就是惊群问题。

一个 `CircuitBreaker` 类已存在于 `services/validation/retry_policy.py` 中，具有标准的 CLOSED → OPEN → HALF_OPEN 状态机，但它仅用于工具执行验证，并未集成到 LLM 调用中。

## Goals / Non-Goals — 目标 / 非目标

**目标：**

- 每个前缀的熔断器，隔离故障的 LLM 路由前缀（例如 `"openai"`、`"anthropic"`、`"bedrock"`），跨所有请求。
- 在回退链中跳过 OPEN 前缀，减少不必要的超时等待延迟。
- 使用 `asyncio.Lock` 进行单探测 HALF_OPEN 恢复，测试故障前缀是否已恢复。
- 可选的 `on_state_change` 回调钩子，用于将来的 EventStore 集成。
- 零破坏性变更到 `LLMService` 公开 API。

**非目标：**

- 多探测 HALF_OPEN 恢复（resilience4j 风格）——鉴于回退链的存在，没有必要。
- EventStore 集成在此变更中——推迟到 P3（特性 15.6）。
- 指标 / 仪表板 / 告警——超出范围。
- 每模型（而非每前缀）粒度。
- 修改 `retry_policy.py` 中现有的 `CircuitBreaker`。

## Decisions — 设计决策

### D1：粒度——按路由前缀，而非按模型或按提供商

熔断器键从 LiteLLM 模型名称前缀中提取：`model.split("/", 1)[0]`。对于没有斜杠的短名称（例如 `"gpt-4o"`、`"claude-3.5-sonnet"`），静态前缀映射表解析正确的前缀。

**为什么按前缀而不是按提供商：** 前缀本身就是故障域。`anthropic/claude-3.5` 和 `bedrock/claude-3.5` 使用相同的底层模型但不同的网络路径。如果 Anthropic API 宕机，`bedrock/` 前缀仍然可用。使用前缀 = 故障域可避免误报隔离。

**为什么按前缀而不是按模型：** 过于细粒度。`openai/gpt-4o` 和 `openai/gpt-4o-mini` 共享同一个 API 端点。如果一个失败，另一个很可能会失败。按前缀正确地对它们分组。

### D2：复用 `retry_policy.py` 中的现有 `CircuitBreaker`

`services/validation/retry_policy.py` 中的 `CircuitBreaker` 已经实现了 CLOSED → OPEN → HALF_OPEN，具有 `failure_threshold`、`recovery_timeout`、`record_success()`、`record_failure()` 和 `allow_request()`。它足够通用以复用用于 LLM 调用。

**考虑的替代方案：** 从头构建一个新的 `LLMCircuitBreaker`。否决——现有代码具有正确的语义且已经过测试。在其上构建管理器层更简单，避免了重复。

### D3：HALF_OPEN——使用 asyncio.Lock 的单探测

当前缀熔断器在 `recovery_timeout` 后进入 HALF_OPEN 时，仅允许一个请求作为探测通过。所有其他并发请求跳过到回退。探测使用 `asyncio.Lock` 确保独占性。

**为什么单探测（Hystrix 风格）而不是多探测（resilience4j 风格）：**

- LLM 有一个回退链——探测失败不会影响用户（他们会获得回退响应）。
- 错误恢复的成本很低：下一个请求失败，熔断器立即重新打开。
- 更简单的实现，更少的边界情况（无计数器、无窗口、无失败率计算）。

### D4：集成点——在 `LLMService` 内部，而非 API 层

熔断器封装了 `LLMService.chat()` 和 `chat_stream()` 内部的 LiteLLM `acompletion()` 调用。在调用 LiteLLM 之前，检查 `breaker.is_open(prefix)`；调用之后，记录成功或失败。

**考虑的替代方案：** 在 API 路由层封装（`api/v1/chat.py`）。否决——熔断器需要模型级别的感知（要检查哪个前缀），而 API 层没有。`LLMService` 是正确的抽象边界。

### D5：回退链过滤

遍历 `fallback_models` 时，跳过任何其前缀熔断器为 OPEN 的模型。这避免了在已知故障的前缀上浪费时间。示例：如果 `openai` 熔断器为 OPEN 且回退链为 `["openai/gpt-4o-mini", "anthropic/claude-3.5"]`，则第一个模型被立即跳过。

### D6：状态变更回调钩子

`CircuitBreakerManager` 接受一个可选的 `on_state_change(prefix: str, old_state: CircuitState, new_state: CircuitState)` 回调。默认为 `None`（无操作）。在 P3 中，此钩子将被接入 `EventStore.append()` 以获得运营可见性。

## Risks / Trade-offs — 风险与权衡

- **[过度激进的隔离]** → 短暂的 429 错误峰值可能不必要地触发熔断器。缓解措施：`failure_threshold` 默认为 5，需要连续 5 次失败才打开。可按部署调整。
- **[短名称误分类]** → 像 `"gpt-4o"` 这样没有前缀的模型名称可能错误映射。缓解措施：维护覆盖常见短名称的前缀映射表；未映射的名称默认为 `"unknown"` 前缀（共享熔断器）。
- **[HALF_OPEN 中的锁争用]** → 在极端并发下，探测锁可能成为瓶颈。缓解措施：只有一个请求等待锁结果；所有其他请求立即进入回退。锁在单个 LLM 调用期间保持（几秒，而非毫秒），但回退请求不会被阻塞。
- **[无持久化]** → 熔断器状态仅在内存中。进程重启将所有熔断器重置为 CLOSED。缓解措施：P2 可接受；P3 可通过 EventStore/Redis 添加状态持久化。