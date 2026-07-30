## Context — 背景

模型调试页面 `/settings/models/debug` 目前支持：
- 模型选择（通过 `/v1/models` 按提供商分组）
- 提示词输入
- 温度滑块（0-2）
- 最大 Token 滑块（1-2000）
- 通过 `POST /api/models/test` 的非流式测试
- 结果显示（内容、模型、用量）

后端 API `POST /api/models/test` 接受 `model_id`、`prompt`、`temperature`、`max_tokens`，返回 `content`、`model`、`usage`、`finish_reason`。它**不**支持流式或系统提示词。

然而，现有 `/v1/chat/completions` 端点同时支持流式和系统消息。我们可以使用此端点而非 `/api/models/test` 来实现增强功能。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 流式响应显示（渐进渲染）
- 用于测试类 Agent 配置的系统提示词字段
- 响应时间测量（毫秒级延迟）
- localStorage 中的测试历史（最近 10 次）
- Token 用量可视化
- 更好的错误消息

**非目标：**
- 后端更改（使用现有 `/v1/chat/completions`）
- 工具调用测试（未来增强）
- 多模型并排比较（未来增强）
- 测试配置的导出/导入

## Decisions — 决策

### D1：使用 `/v1/chat/completions` 而非 `/api/models/test`

**决策**：从 `POST /api/models/test` 切换到 `POST /v1/chat/completions`，设置 `stream: true`。

**理由**：chat completions 端点已支持流式、系统消息，并返回相同的数据。无需后端更改。

**考虑过的替代方案**：
- 扩展 `/api/models/test` 以支持流式 — 增加后端复杂度但无收益
- 同时使用两个端点（流式走 chat，非流式走 test）— 用户体验不一致

### D2：通过现有的 `api.stream()` 客户端方法实现流式

**决策**：使用现有的 `api.stream()` 方法进行渐进渲染。

**理由**：已在 `api-client.ts` 中实现，处理 SSE 解析，生成内容块。

### D3：测试历史存储在 localStorage（而非数据库）

**决策**：在 localStorage 中存储最近 10 次测试，包括模型、提示词、系统提示词、参数、结果和时间戳。

**理由**：无需后端更改。测试是临时的调试产物，而非持久数据。localStorage 对于会话级别历史已足够。

**考虑过的替代方案**：
- 数据库存储 — 对调试工具来说过度设计，增加 API 复杂度
- 无历史 — 用户导航离开后失去上下文

### D4：通过 `Date.now()` 测量响应时间

**决策**：在请求开始前和收到第一个/最后一个分块后，使用 `Date.now()` 测量延迟。

**理由**：简单，无需后端更改。显示首字节时间（TTFT）和总时间。

## Risks / Trade-offs — 风险 / 权衡

- **[流式需要不同的 UI 状态]** → 需要处理渐进式内容渲染，显示"输入中"指示器，流式进行时禁用控件
- **[localStorage 大小限制]** — 10 次含完整响应内容的测试可能数据量大。缓解措施：历史记录中将内容截断至 500 字符
- **[通过 `/v1/chat/completions` 使用系统提示词]** — 该端点使用 `get_current_user_id` 依赖，而非 `verify_api_key`。需要在前端正确处理认证
