## Context — 背景

Hecate 的 engine 层目前使用泛型 Python 异常（ValueError、KeyError、RuntimeError），没有领域特定类型。现有的 `ErrorClassifier` 位于 `services/validation/retry_policy.py` 中，通过字符串关键字匹配（检查错误消息中是否包含 "timeout"、"rate limit"、"429" 等）来分类错误。

原始 1.3.5g 规范要求完整的异常层级：`HecateError → LLMError/ToolError/EngineError/SecurityError/ChannelError`，包含约 15 个异常子类型。然而，对 10 个平台的研究表明，这种做法并非行业实践：

- **0/10 平台** 将提供商异常包装到自己的 LLMError 树中
- OpenAI SDK 使用基于状态码的层级（RateLimitError=429, AuthenticationError=401）
- LiteLLM 直接扩展 OpenAI 的层级
- LangChain 使用双重继承映射（例如 `OpenAIContextOverflowError(openai.BadRequestError, ContextOverflowError)`）
- Google ADK 让 Gemini 错误未经包装直接通过，使用 `ToolErrorType` 枚举进行语义分类
- LangGraph 只定义图形特定错误（GraphRecursionError, NodeTimeoutError, InvalidUpdateError）

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 定义 `HecateError` 基类，包含 Hecate 特定的异常类别
- 用类型化的 Hecate 异常替换泛型异常（RuntimeError, KeyError）
- 定义 `ErrorCategory` 枚举用于语义错误分类
- 升级 `ErrorClassifier` 以支持基于 isinstance 的匹配及字符串回退
- 保持完全向后兼容——所有现有的 except 块继续工作

**非目标：**
- LLMError/ToolError 异常树（推迟——行业共识是让提供商异常直接通过）
- 双重继承提供商映射（P4——LangChain 模式，需要提供商特定类）
- 框架级自动重试（1.3.5h——单独变更，依赖此变更）
- 平台级工具门控（1.3.5f——独立）
- 重构 API 层错误处理（单独变更）

## Decisions — 决策

### D1: 只定义 Hecate 特定异常，不定义 LLM/Tool 包装器

**选择**：仅定义 `HecateError → EngineError/ChannelError/SecurityError`。不创建 LLMError 或 ToolError 异常树。

**理由**：10 个平台的研究显示，零个平台包装提供商异常。Hecate 使用 LiteLLM，它已经继承了 OpenAI 的类型化异常（RateLimitError, AuthenticationError 等）。将它们包装在 `LLMRateLimitError` 中增加了一层无价值的抽象——`isinstance(e, openai.RateLimitError)` 已经可以工作。

**考虑的替代方案**：
- 完整树（原始规范）：拒绝——没有行业先例，engine 层无法导入提供商 SDK（层级约束）
- 双重继承（LangChain 模式）：推迟到 P4——需要在 services 层提供提供商特定的映射类

### D2: ErrorCategory 枚举取代 LLMError/ToolError 进行分类

**选择**：定义 `ErrorCategory` StrEnum，包含所有错误源（LLM, Tool, Engine, Security, Channel）的语义类别。`ErrorClassifier.classify()` 返回 `ErrorCategory` 而不是 bool。

**理由**：Google ADK 的 `ToolErrorType` 枚举证明，通过枚举进行语义分类足以支持重试决策、可观测性和错误报告。它避免了完整异常树的复杂性，同时提供相同的分类能力。

### D3: ErrorClassifier 原地升级，向后兼容

**选择**：扩展 `services/validation/retry_policy.py` 中现有的 `ErrorClassifier`，新增 `classify(error) -> ErrorCategory` 方法。保留现有的 `is_retryable(error_string)` 方法及字符串回退。

**理由**：ErrorClassifier 已被 RetryPolicy 和 CircuitBreaker 使用。升级在回退到字符串匹配之前增加了 isinstance 检查。对现有调用者没有破坏性变更。

### D4: GraphValidationError 继承变更

**选择**：将 `GraphValidationError(Exception)` 改为 `GraphValidationError(EngineError)`。

**理由**：EngineError 继承自 HecateError 继承自 Exception。Python 的异常处理会检查继承链，因此 `except GraphValidationError` 和 `except Exception` 都继续工作。唯一的行为变化：`except EngineError` 现在也会捕获 GraphValidationError（这正是期望的行为）。

### D5: MaxSuperstepsError 和 ChannelNotFoundError 替换泛型异常

**选择**：将 `pregel.py` 中的 `raise RuntimeError(...)` 替换为 `raise MaxSuperstepsError(...)`。将 `channel.py` 中的 `raise KeyError(...)` 替换为 `raise ChannelNotFoundError(...)`。

**理由**：LangGraph 对类似情况使用 `GraphRecursionError(RecursionError)` 和 `InvalidUpdateError`。类型化异常允许 API 层捕获特定的 engine 错误以进行适当的 HTTP 状态映射。

### D6: GuardrailBlockedError 与现有的 GuardrailAction.BLOCK

**选择**：定义 `GuardrailBlockedError(SecurityError)` 作为可选的异常，可由 guardrail hook 抛出，同时保留现有的基于 `GuardrailResult(action=BLOCK)` 返回值的模式。

**理由**：当前基于返回值的模式（PreLLMHook 返回带有 action=BLOCK 的 GuardrailResult）是主要机制。GuardrailBlockedError 为偏好基于异常的控制流的代码路径提供了替代方案。两种模式共存。

## Risks / Trade-offs — 风险 / 权衡

| 风险 | 缓解措施 |
|------|------------|
| GraphValidationError 继承变更可能破坏 isinstance 检查 | Python 继承链：GraphValidationError → EngineError → HecateError → Exception。所有现有的 except 块都能工作。 |
| ErrorClassifier 的 isinstance 检查需要导入提供商 SDK | Classifier 位于 services/ 层（不是 engine/），因此导入 openai/litellm 是允许的。 |
| ErrorCategory 枚举可能无法覆盖所有边界情况 | UNKNOWN 类别作为回退。保留基于字符串的匹配以处理未识别的错误。 |
| GuardrailBlockedError 与 GuardrailAction.BLOCK 重复 | 两者按设计共存——基于返回值的方式用于 hook，基于异常的方式用于直接调用。 |
