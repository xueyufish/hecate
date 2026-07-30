## ADDED Requirements — 新增需求

### Requirement: HecateError base exception class — HecateError 基础异常类

engine 层 SHALL 在 `engine/errors.py` 中定义 `HecateError(Exception)` 作为所有 Hecate 特定错误的基础异常。所有 Hecate 异常类型 SHALL 继承自 `HecateError`。

#### Scenario: Catch all Hecate errors — 捕获所有 Hecate 错误

- **WHEN** 抛出任何 Hecate 特定异常
- **THEN** `except HecateError` SHALL 捕获它

#### Scenario: Backward compatibility with Exception — 与 Exception 的向后兼容

- **WHEN** 代码使用 `except Exception`
- **THEN** 所有 HecateError 子类 SHALL 被捕获（HecateError 继承自 Exception）

### Requirement: EngineError category for engine runtime errors — EngineError engine 运行时错误类别

engine 层 SHALL 定义 `EngineError(HecateError)` 作为 engine 特定运行时错误的基类。子类型：`MaxSuperstepsError(EngineError)`，用于图形执行超过最大超步数。

#### Scenario: Max supersteps exceeded — 超过最大超步数

- **WHEN** 图形执行超过 `max_supersteps`
- **THEN** PregelRuntime SHALL 抛出 `MaxSuperstepsError` 而不是通用的 `RuntimeError`
- **AND** `except EngineError` SHALL 捕获它

#### Scenario: Existing RuntimeError code unaffected — 现有的 RuntimeError 代码不受影响

- **WHEN** 其他代码因非 engine 条件抛出 `RuntimeError`
- **THEN** 它 SHALL NOT 被 `except EngineError` 捕获

### Requirement: GraphValidationError inherits EngineError — GraphValidationError 继承 EngineError

`GraphValidationError` SHALL 将其继承从 `Exception` 改为 `EngineError`。`field` 属性 SHALL 保留。

#### Scenario: Existing GraphValidationError catch still works — 现有的 GraphValidationError 捕获仍然有效

- **WHEN** 代码使用 `except GraphValidationError`
- **THEN** 它 SHALL 继续捕获图形验证错误

#### Scenario: Catchable as EngineError — 可作为 EngineError 捕获

- **WHEN** 代码使用 `except EngineError`
- **THEN** 它 SHALL 捕获 GraphValidationError 实例

#### Scenario: Field attribute preserved — 保留 field 属性

- **WHEN** GraphValidationError 使用 field 参数抛出
- **THEN** `field` 属性 SHALL 在异常实例上可访问

### Requirement: ChannelError category for channel operation failures — ChannelError 通道操作失败类别

engine 层 SHALL 定义 `ChannelError(HecateError)` 及其子类型 `ChannelNotFoundError(ChannelError)`。ChannelManager SHALL 在从未注册的通道读取时抛出 `ChannelNotFoundError` 而不是裸 `KeyError`。

#### Scenario: Read from unregistered channel — 从未注册的通道读取

- **WHEN** 调用 `channel_manager.read("unknown")`
- **THEN** `ChannelNotFoundError` SHALL 被抛出
- **AND** `except ChannelError` SHALL 捕获它

#### Scenario: ChannelNotFoundError is also catchable as KeyError — ChannelNotFoundError 也可作为 KeyError 捕获

- **WHEN** `ChannelNotFoundError` 被抛出
- **THEN** `except KeyError` SHALL 捕获它（ChannelNotFoundError 同时继承自 ChannelError 和 KeyError）

### Requirement: SecurityError category for guardrail and security failures — SecurityError guardrail 和安全失败类别

engine 层 SHALL 定义 `SecurityError(HecateError)` 及其子类型 `GuardrailBlockedError(SecurityError)`，用于 guardrail 拦截的请求。

#### Scenario: Guardrail blocks request — Guardrail 拦截请求

- **WHEN** guardrail hook 确定请求应被拦截
- **THEN** `GuardrailBlockedError` MAY 被抛出（与现有的基于返回值的 GuardrailResult 模式并存）
- **AND** `except SecurityError` SHALL 捕获它

#### Scenario: Coexistence with GuardrailResult — 与 GuardrailResult 共存

- **WHEN** PreLLMHook 返回 `GuardrailResult(action=BLOCK)`
- **THEN** 现有的基于返回值的模式 SHALL 继续工作，无需抛出 GuardrailBlockedError

### Requirement: ErrorCategory enum for semantic error classification — ErrorCategory 语义错误分类枚举

engine 层 SHALL 将 `ErrorCategory` 定义为 `StrEnum`，包含以下成员：`LLM_RATE_LIMIT`、`LLM_AUTH`、`LLM_TIMEOUT`、`LLM_CONTEXT_WINDOW`、`TOOL_TIMEOUT`、`TOOL_NOT_FOUND`、`TOOL_EXECUTION`、`ENGINE`、`SECURITY`、`CHANNEL`、`UNKNOWN`。

#### Scenario: String comparison — 字符串比较

- **WHEN** `ErrorCategory.LLM_RATE_LIMIT == "llm_rate_limit"`
- **THEN** 比较结果为 `True`

#### Scenario: Unknown error category — 未知错误类别

- **WHEN** 错误无法分类到任何特定类别
- **THEN** 分类器 SHALL 返回 `ErrorCategory.UNKNOWN`

### Requirement: ErrorClassifier upgraded with isinstance-based classification — ErrorClassifier 升级为基于 isinstance 的分类

`services/validation/retry_policy.py` 中的 `ErrorClassifier` SHALL 被扩展，增加 `classify(error: Exception) -> ErrorCategory` 方法，使用 isinstance 检查对提供商 SDK 异常类型进行分类。保留现有的 `is_retryable(error: str) -> bool` 方法以确保向后兼容。

#### Scenario: Classify OpenAI rate limit error — 分类 OpenAI 速率限制错误

- **WHEN** 调用 `classify(openai.RateLimitError(...))`
- **THEN** 它 SHALL 返回 `ErrorCategory.LLM_RATE_LIMIT`

#### Scenario: Classify HecateError subtypes — 分类 HecateError 子类型

- **WHEN** 调用 `classify(MaxSuperstepsError(...))`
- **THEN** 它 SHALL 返回 `ErrorCategory.ENGINE`

#### Scenario: String fallback for unrecognized errors — 未识别错误的字符串回退

- **WHEN** 调用 `classify(ValueError("timeout"))`
- **THEN** 它 SHALL 回退到基于字符串的关键字匹配
- **AND** 如果匹配到 "timeout"，返回 `ErrorCategory.LLM_TIMEOUT`

#### Scenario: is_retryable uses classify — is_retryable 使用 classify

- **WHEN** 调用 `is_retryable(error_string)`
- **THEN** 它 SHALL 继续使用字符串输入工作（向后兼容）
- **AND** `is_retryable_exception(exception)` SHALL 使用新的 classify 方法
