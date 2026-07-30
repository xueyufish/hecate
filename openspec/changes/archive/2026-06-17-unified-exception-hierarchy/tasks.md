## 1. 异常层级定义

- [x] 1.1 创建 `src/hecate/engine/errors.py`，包含 `HecateError(Exception)` 基类
- [x] 1.2 定义 `EngineError(HecateError)` 及其子类型 `MaxSuperstepsError(EngineError)`
- [x] 1.3 定义 `ChannelError(HecateError)` 及其子类型 `ChannelNotFoundError(ChannelError, KeyError)`
- [x] 1.4 定义 `SecurityError(HecateError)` 及其子类型 `GuardrailBlockedError(SecurityError)`
- [x] 1.5 定义 `ErrorCategory(StrEnum)` 包含所有成员（LLM_RATE_LIMIT, LLM_AUTH, LLM_TIMEOUT, LLM_CONTEXT_WINDOW, TOOL_TIMEOUT, TOOL_NOT_FOUND, TOOL_EXECUTION, ENGINE, SECURITY, CHANNEL, UNKNOWN）
- [x] 1.6 所有类完整文档字符串（模块、公共类）

## 2. GraphValidationError 迁移

- [x] 2.1 将 `engine/graph_dsl.py` 中的 `GraphValidationError` 继承从 `Exception` 改为 `EngineError`
- [x] 2.2 移动导入：`from hecate.engine.errors import EngineError`（将 GraphValidationError 保留在 graph_dsl.py 中以保持导入兼容性）
- [x] 2.3 保留 `field` 属性和 `__init__` 签名
- [x] 2.4 验证所有现有测试通过（GraphValidationError 仍可被捕获为 Exception 和 GraphValidationError）

## 3. Engine 错误替换

- [x] 3.1 在 `engine/pregel.py` 中：将最大超步数的 `raise RuntimeError(...)` 替换为 `raise MaxSuperstepsError(...)`
- [x] 3.2 在 `engine/channel.py` 中：将未注册通道读取的 `raise KeyError(...)` 替换为 `raise ChannelNotFoundError(...)`
- [x] 3.3 从 `hecate.engine.errors` 导入 `MaxSuperstepsError` 和 `ChannelNotFoundError`

## 4. ErrorClassifier 升级

- [x] 4.1 向 ErrorClassifier 添加 `classify(error: Exception) -> ErrorCategory` 方法，使用 isinstance 检查
- [x] 4.2 实现 HecateError 子类型的 isinstance 映射（EngineError→ENGINE, ChannelError→CHANNEL, SecurityError→SECURITY）
- [x] 4.3 实现提供商 SDK 异常的 isinstance 映射（openai.RateLimitError→LLM_RATE_LIMIT, openai.AuthenticationError→LLM_AUTH 等），使用 try/except ImportError 保护
- [x] 4.4 使用现有关键字列表实现未识别异常的基于字符串的回退
- [x] 4.5 添加 `is_retryable_exception(error: Exception) -> bool`，使用 classify() 而不是字符串匹配
- [x] 4.6 保留现有 `is_retryable(error: str) -> bool` 以确保向后兼容
- [x] 4.7 从 hecate.engine.errors 导入 ErrorCategory

## 5. 测试

- [x] 5.1 测试 HecateError 可作为 Exception 捕获
- [x] 5.2 测试 EngineError 子类型（MaxSuperstepsError 抛出和捕获）
- [x] 5.3 测试 ChannelNotFoundError 可同时作为 ChannelError 和 KeyError 捕获
- [x] 5.4 测试 GraphValidationError 继承 EngineError（可作为 EngineError、HecateError、Exception、GraphValidationError 捕获）
- [x] 5.5 测试 GuardrailBlockedError 继承 SecurityError
- [x] 5.6 测试 ErrorCategory 枚举字符串比较
- [x] 5.7 测试 ErrorClassifier.classify 使用 HecateError 子类型
- [x] 5.8 测试 ErrorClassifier.classify 使用提供商异常类型（模拟或真实）
- [x] 5.9 测试 ErrorClassifier.classify 对未识别异常的字符串回退
- [x] 5.10 测试 ErrorClassifier.is_retryable_exception 对可重试类别
- [x] 5.11 测试 ErrorClassifier.is_retryable (string) 向后兼容性
- [x] 5.12 测试 PregelRuntime 抛出 MaxSuperstepsError（而非 RuntimeError）
- [x] 5.13 测试 ChannelManager 抛出 ChannelNotFoundError（而非 KeyError）

## 6. 文档

- [x] 6.1 更新 AGENTS.md：如有必要，将 engine/errors.py 添加到关键文件表
- [x] 6.2 验证没有 engine 层违规（errors.py 不得从 services/ 或 models/ 导入）
