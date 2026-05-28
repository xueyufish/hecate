## Context

P1 的工具执行只有基础异常处理（try/except），没有结果校验、没有智能重试、没有输出 Schema 校验。Context Engineering 的 EvidenceTracker 已经捕获工具结果，但不做校验。

## Goals / Non-Goals

**Goals:**

1. 实现 ResultValidator — JSON Schema 校验工具输出
2. 实现 RetryPolicy — 可配置重试策略（指数退避、错误分类）
3. 实现 OutputSchemaValidator — LLM 输出 Schema 校验
4. 集成到 ConversationService

**Non-Goals:**

1. 不实现工具沙箱（属于 P3）
2. 不修改现有工具接口（纯新增）

## Decisions

### D1: 使用 jsonschema 库做 Schema 校验

**选择**：复用已有 jsonschema 依赖

**理由**：
- 已在 pyproject.toml 中
- 支持 JSON Schema Draft 7
- 错误信息清晰

### D2: 重试策略使用策略模式

**选择**：每种重试策略一个类，可配置

**理由**：
- 不同工具可能需要不同重试策略
- 网络错误 vs 业务错误重试逻辑不同
- 可扩展

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| 重试导致延迟增加 | 设置最大重试次数和总超时 |
| Schema 校验性能 | 缓存 Schema 编译结果 |
| 错误分类不准确 | 提供默认分类 + 自定义覆盖 |
