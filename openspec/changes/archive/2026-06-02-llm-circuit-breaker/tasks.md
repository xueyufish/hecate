## 1. Core: CircuitBreakerManager — 核心：CircuitBreakerManager

- [x] 1.1 创建 `src/hecate/services/llm/circuit_breaker.py`，包含 `CircuitBreakerManager` 类骨架：`__init__`（failure_threshold、recovery_timeout、on_state_change 回调）、`_breakers: dict[str, CircuitBreaker]`、`_locks: dict[str, asyncio.Lock]`
- [x] 1.2 实现 `_extract_prefix(model: str) -> str`，使用基于斜杠的提取和静态短名称映射表
- [x] 1.3 实现 `get_breaker(prefix: str) -> CircuitBreaker`，使用锁保护的懒创建（并发新前缀请求的线程安全）
- [x] 1.4 实现 `is_open(model: str) -> bool` — 提取前缀，检查熔断器状态
- [x] 1.5 实现 `record_success(model: str) -> None` — 提取前缀，委托给熔断器，如果状态变更则调用 on_state_change
- [x] 1.6 实现 `record_failure(model: str) -> None` — 提取前缀，委托给熔断器，如果状态变更则调用 on_state_change
- [x] 1.7 实现 `acquire_probe(prefix: str) -> bool` — 获取 HALF_OPEN 探测的 asyncio.Lock；如果已被持有则返回 False（调用者应回退）
- [x] 1.8 实现 `release_probe(prefix: str) -> None` — 释放探测锁

## 2. Integration: LLMService — 集成：LLMService

- [x] 2.1 向 `LLMService.__init__` 添加可选的 `circuit_breaker: CircuitBreakerManager | None = None` 参数
- [x] 2.2 修改 `chat()` — 在 LiteLLM 调用前：检查 `circuit_breaker.is_open(model)`，如果 OPEN 则跳转到回退；如果 HALF_OPEN，则尝试使用锁进行探测
- [x] 2.3 修改 `chat()` — LiteLLM 成功后：调用 `circuit_breaker.record_success(model)`
- [x] 2.4 修改 `chat()` — LiteLLM 失败后：调用 `circuit_breaker.record_failure(model)`
- [x] 2.5 修改 `chat_stream()` — 与 chat() 相同的熔断器集成：OPEN→跳过，HALF_OPEN→探测，记录成功/失败
- [x] 2.6 修改 `_try_fallback()` — 跳过其前缀熔断器为 OPEN 的模型；为每次失败的回退尝试记录失败
- [x] 2.7 修改 `_try_fallback_stream()` — 与 _try_fallback() 相同的回退过滤和失败记录

## 3. Feature Catalog — 功能目录

- [x] 3.1 向 `docs/features/feature-catalog.md` 添加 P3 特性条目 15.6 "熔断事件集成"，注明依赖 1.3.10 + 15.1

## 4. Tests — 测试

- [x] 4.1 创建 `tests/test_services/test_llm/test_circuit_breaker.py`，包含测试文件设置（导入、fixtures）
- [x] 4.2 测试 `_extract_prefix` — 基于斜杠、短名称映射、未知回退
- [x] 4.3 测试 `CircuitBreakerManager` 懒创建和线程安全（并发新前缀）
- [x] 4.4 测试熔断器状态转换：CLOSED→OPEN（阈值）、OPEN→HALF_OPEN（超时）、HALF_OPEN→CLOSED（成功）、HALF_OPEN→OPEN（失败）
- [x] 4.5 测试单探测 HALF_OPEN：一个请求通过，并发请求跳到回退
- [x] 4.6 测试回退链过滤：OPEN 前缀模型被跳过
- [x] 4.7 测试 `LLMService.chat()` 集成：OPEN→跳过，HALF_OPEN→探测，成功/失败记录
- [x] 4.8 测试 `LLMService.chat_stream()` 集成：与 chat() 相同但针对流式路径
- [x] 4.9 测试状态转换时的 `on_state_change` 回调调用
- [x] 4.10 测试不带熔断器的 `LLMService`：行为与当前实现相同（无回归）
- [x] 4.11 测试所有前缀 OPEN：引发 `RuntimeError("All models failed")`

## 5. Verification — 验证

- [x] 5.1 运行 `ruff check src/hecate/ tests/` — 零错误
- [x] 5.2 运行 `ruff format --check src/ tests/` — 零错误
- [x] 5.3 运行 `mypy src/` — 零错误
- [x] 5.4 运行 `python -m pytest tests/ -q` — 全部通过