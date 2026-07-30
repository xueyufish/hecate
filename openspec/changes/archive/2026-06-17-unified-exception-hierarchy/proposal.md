## Why — 为什么

Hecate 的 engine 层使用泛型 Python 异常（ValueError、KeyError、RuntimeError），无法在异常级别区分错误来源。现有的 `ErrorClassifier` 位于 `services/validation/retry_policy.py` 中，通过字符串关键字匹配来分类错误——脆弱且间接。API 层的错误处理依赖于临时的 if-else 字符串检查，而非结构化的异常类型。

对 10 个平台（OpenAI SDK、LiteLLM、LangChain、LangGraph、Google ADK、IBM watsonx、Salesforce、Huawei、AutoGen、CrewAI）的研究表明，**没有平台将提供商异常包装到自己的 LLMError/ToolError 树中**。所有平台都让提供商的 SDK 异常直接通过。行业共识是：只定义你自己的领域特定错误（engine、channel、security），使用错误类别枚举进行分类，并升级分类器以使用 isinstance 检查。

## What Changes — 变更内容

- 在新的 `engine/errors.py` 中定义 `HecateError(Exception)` 作为所有 Hecate 特定错误的基础异常
- 定义三个 Hecate 特定的异常类别：`EngineError`、`ChannelError`、`SecurityError` 及其子类型
- 将 `GraphValidationError` 的继承从 `Exception` 改为 `EngineError`（向后兼容——EngineError 继承自 HecateError 继承自 Exception）
- 定义 `ErrorCategory` StrEnum，包含语义类别（LLM_RATE_LIMIT, LLM_AUTH, LLM_TIMEOUT, TOOL_TIMEOUT, ENGINE, SECURITY 等）
- 升级 `ErrorClassifier` 以支持基于 isinstance 的类型匹配，支持提供商 SDK 异常（openai.RateLimitError 等），同时保留基于字符串的回退
- 将 PregelRuntime 的 `MaxSupersteps` 错误从通用的 `RuntimeError` 更新为 `MaxSuperstepsError(EngineError)`

## Capabilities — 能力

### 新增能力

- `exception-hierarchy`：HecateError 基类，包含 EngineError/ChannelError/SecurityError 子类型、ErrorCategory 枚举和升级后的 ErrorClassifier

### 修改的能力

（无——现有的 guardrail-hook、engine-types、channel-registry 规范保持不变；错误分类是新增的）

## Impact — 影响

- **新文件**：`src/hecate/engine/errors.py`——异常层级 + ErrorCategory 枚举
- **修改**：`src/hecate/services/validation/retry_policy.py`——ErrorClassifier 升级，支持 isinstance
- **修改**：`src/hecate/engine/graph_dsl.py`——GraphValidationError 继承 EngineError
- **修改**：`src/hecate/engine/pregel.py`——MaxSuperstepsError 替换 RuntimeError
- **修改**：`src/hecate/engine/channel.py`——ChannelNotFoundError 替换裸 KeyError
- **测试**：异常层级、ErrorCategory 分类、ErrorClassifier isinstance 匹配的新测试
- **无破坏性变更**：所有现有的 except 块继续工作（HecateError 继承自 Exception）
