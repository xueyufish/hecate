## ADDED Requirements — 新增需求

### Requirement：每个前缀的熔断器管理 — 每个前缀的熔断器管理
系统 SHALL 为从 LiteLLM 模型名称中提取的每个路由前缀维护一个单独的 `CircuitBreaker` 实例。前缀是 `"/"` 之前的第一个段（例如，`"openai/gpt-4o"` 中的 `"openai"`）。对于没有斜杠的模型名称，系统 SHALL 通过静态映射表解析前缀（`gpt→openai`、`claude→anthropic`、`gemini→gemini`、`deepseek→deepseek`）。未映射的名称 SHALL 默认为 `"unknown"`。

#### Scenario：带斜杠的前缀提取
- **WHEN** 模型名称为 `"openai/gpt-4o"`
- **THEN** 前缀为 `"openai"`

#### Scenario：不带斜杠的前缀提取
- **WHEN** 模型名称为 `"gpt-4o"`
- **THEN** 前缀为 `"openai"`（通过映射表）

#### Scenario：未知的短名称
- **WHEN** 模型名称为 `"some-new-model"` 且没有映射
- **THEN** 前缀为 `"unknown"`

#### Scenario：懒创建熔断器
- **WHEN** 首次看到某个前缀
- **THEN** 为该前缀创建一个新的 `CircuitBreaker` 实例，使用默认阈值

### Requirement：熔断器状态机 — 熔断器状态机
每个前缀的熔断器 SHALL 遵循标准的三态机：CLOSED（请求通过）、OPEN（请求被拒绝）、HALF_OPEN（允许一个探测请求）。当记录到 `failure_threshold` 次连续失败时，熔断器 SHALL 从 CLOSED 转换到 OPEN。在 `recovery_timeout` 秒过后，熔断器 SHALL 从 OPEN 转换到 HALF_OPEN。熔断器 SHALL 在探测成功时从 HALF_OPEN 转换到 CLOSED，或在探测失败时回到 OPEN。

#### Scenario：连续失败时 CLOSED 到 OPEN
- **WHEN** 为前缀 `"openai"` 记录了 5 次连续失败（默认阈值）
- **THEN** 熔断器状态转换到 OPEN

#### Scenario：超时后 OPEN 到 HALF_OPEN
- **WHEN** 熔断器为 OPEN 且 `recovery_timeout`（默认 30 秒）已过
- **THEN** 熔断器状态变为 HALF_OPEN

#### Scenario：成功时 HALF_OPEN 到 CLOSED
- **WHEN** 熔断器为 HALF_OPEN 且探测请求成功
- **THEN** 熔断器状态转换到 CLOSED，失败计数重置

#### Scenario：失败时 HALF_OPEN 到 OPEN
- **WHEN** 熔断器为 HALF_OPEN 且探测请求失败
- **THEN** 熔断器状态回到 OPEN，`recovery_timeout` 重新开始

### Requirement：使用 asyncio.Lock 的单探测 HALF_OPEN — 使用 asyncio.Lock 的单探测 HALF_OPEN
当前缀熔断器处于 HALF_OPEN 状态时，系统 SHALL 允许恰好一个并发请求作为探测通过。同一前缀的所有其他并发请求 SHALL 立即跳到回退。探测请求 SHALL 在执行前获取一个 `asyncio.Lock`。

#### Scenario：单个探测通过
- **WHEN** 熔断器为 HALF_OPEN 且一个请求到达
- **THEN** 锁被获取，请求调用 LLM，熔断器记录结果

#### Scenario：并发请求跳到回退
- **WHEN** 熔断器为 HALF_OPEN 且探测正在进行中
- **THEN** 同一前缀的其他请求跳到回退，不等待

### Requirement：回退链过滤 — 回退链过滤
当遍历回退链时（`_try_fallback` / `_try_fallback_stream`），系统 SHALL 跳过任何其前缀熔断器处于 OPEN 状态的模型。其前缀熔断器为 CLOSED 或 HALF_OPEN 的模型 SHALL 正常尝试。

#### Scenario：在回退中跳过 OPEN 前缀
- **WHEN** 回退链为 `["openai/gpt-4o-mini", "anthropic/claude-3.5"]` 且 `"openai"` 熔断器为 OPEN
- **THEN** `"openai/gpt-4o-mini"` 被跳过，`"anthropic/claude-3.5"` 被尝试

#### Scenario：所有前缀均为 OPEN
- **WHEN** 所有回退模型都具有 OPEN 熔断器
- **THEN** 引发 `RuntimeError("All models failed")`

#### Scenario：回退中 CLOSED 的前缀
- **WHEN** 回退模型具有 CLOSED 熔断器
- **THEN** 该模型正常尝试

### Requirement：LLMService 集成 — LLMService 集成
`LLMService` SHALL 在其构造函数中接受一个可选的 `CircuitBreakerManager`。当存在时，`chat()` 和 `chat_stream()` SHALL 在调用 LiteLLM 前检查熔断器状态。如果主模型的前缀熔断器为 OPEN，调用 SHALL 直接跳到回退。每次 LiteLLM 调用（成功或失败）后，熔断器 SHALL 记录结果。

#### Scenario：主模型前缀为 OPEN
- **WHEN** 使用模型 `"openai/gpt-4o"` 调用 `chat()` 且 `"openai"` 熔断器为 OPEN
- **THEN** LiteLLM 不被调用；立即遍历回退链

#### Scenario：成功调用记录成功
- **WHEN** 模型 `"openai/gpt-4o"` 的 LiteLLM 调用成功
- **THEN** 在熔断器上调用 `record_success("openai/gpt-4o")`

#### Scenario：失败调用记录失败
- **WHEN** 模型 `"openai/gpt-4o"` 的 LiteLLM 调用失败
- **THEN** 在熔断器上调用 `record_failure("openai/gpt-4o")`

#### Scenario：未配置熔断器
- **WHEN** 构建 `LLMService` 时没有 `CircuitBreakerManager`
- **THEN** 行为与当前实现相同（无熔断器检查）

### Requirement：状态变更回调钩子 — 状态变更回调钩子
`CircuitBreakerManager` SHALL 接受一个可选的 `on_state_change` 回调，类型为 `Callable[[str, CircuitState, CircuitState], None]`。当任何前缀熔断器状态转换时，SHALL 使用 `(prefix, old_state, new_state)` 调用该回调。如果未提供回调，状态转换静默发生。

#### Scenario：状态变更时调用回调
- **WHEN** `"openai"` 熔断器从 CLOSED 转换到 OPEN
- **THEN** 调用 `on_state_change("openai", CircuitState.CLOSED, CircuitState.OPEN)`

#### Scenario：未配置回调
- **WHEN** `on_state_change` 为 `None` 且熔断器转换
- **THEN** 不调用回调；熔断器正常转换

### Requirement：线程安全 — 线程安全
`CircuitBreakerManager` SHALL 在异步上下文中可安全并发使用。新前缀的熔断器创建 SHALL 受到保护，防止竞态条件（两个并发请求为同一前缀创建重复的熔断器）。

#### Scenario：新前缀的并发请求
- **WHEN** 两个请求同时到达同一没有现有熔断器的前缀
- **THEN** 恰好为该前缀创建一个熔断器实例