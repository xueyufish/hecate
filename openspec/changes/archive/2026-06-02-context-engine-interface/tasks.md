## 1. ContextEngine ABC — ContextEngine ABC

- [x] 1.1 创建 `src/hecate/engine/context.py`，包含定义抽象方法的 `ContextEngine(ABC)`：`select_messages(history: list[dict], budget: int) -> list[dict]`、`compress(messages: list[dict]) -> list[dict]`、`estimate_tokens(messages: list[dict]) -> int`
- [x] 1.2 为 ContextEngine ABC 和每个抽象方法添加完整的文档字符串

## 2. InMemoryContextEngine — InMemoryContextEngine 实现

- [x] 2.1 使用简单启发式方法实现 `InMemoryContextEngine(ContextEngine)`
- [x] 2.2 `select_messages` 保留适合 token 预算的最新的消息
- [x] 2.3 `compress` 在消息数量超过阈值时移除最旧的消息（默认 50）
- [x] 2.4 `estimate_tokens` 使用基于字符的估算（len(text) // 4）
- [x] 2.5 处理边界情况：空列表、零预算、包含 None 内容的消息
- [x] 2.6 添加文档字符串

## 3. Tests — 测试

- [x] 3.1 创建 `tests/test_engine/test_context.py`
- [x] 3.2 测试 ContextEngine 是抽象的（不能直接实例化）
- [x] 3.3 测试 InMemoryContextEngine.select_messages 在预算内返回最近的消息
- [x] 3.4 测试 InMemoryContextEngine.select_messages 空列表返回空
- [x] 3.5 测试 InMemoryContextEngine.select_messages 零预算返回空
- [x] 3.6 测试 InMemoryContextEngine.compress 减少消息数量
- [x] 3.7 测试 InMemoryContextEngine.estimate_tokens 返回合理的估算值
- [x] 3.8 测试 InMemoryContextEngine.estimate_tokens 空列表返回 0

## 4. Verification — 验证

- [x] 4.1 运行 `ruff check src/hecate/engine/context.py tests/test_engine/test_context.py`
- [x] 4.2 运行 `ruff format --check src/hecate/engine/context.py tests/test_engine/test_context.py`
- [x] 4.3 运行 `mypy src/hecate/engine/context.py`
- [x] 4.4 运行 `python -m pytest tests/test_engine/test_context.py -v`
- [x] 4.5 运行完整测试套件 `python -m pytest tests/ -q` 以验证无回归