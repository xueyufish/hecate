## Why

P1 的工具执行只有基础异常处理，没有结果校验、没有智能重试、没有输出 Schema 校验。这导致工具调用失败时无法自动恢复，输出格式错误时无法检测。

## What Changes

- **新增 `ResultValidator`**：JSON Schema 校验工具输出，支持自定义校验规则
- **新增 `RetryPolicy`**：可配置的重试策略（指数退避、错误分类、最大重试次数）
- **新增 `OutputSchemaValidator`**：LLM 输出的 Schema 校验，检测格式错误
- **扩展 `ConversationService`**：集成校验和重试逻辑

## Capabilities

### New Capabilities

- `result-validation`: 工具输出 JSON Schema 校验 + 自定义校验规则
- `retry-policy`: 可配置重试策略（指数退避、错误分类、熔断）
- `output-schema-validation`: LLM 输出 Schema 校验

### Modified Capabilities

（无）

## Impact

- **新增代码**：`services/validation/` 目录，约 400-600 行
- **修改代码**：`services/conversation.py`（集成校验和重试）
- **新增依赖**：无（复用已有 jsonschema）
- **零破坏性变更**：现有工具调用行为不变，校验和重试是可选增强
