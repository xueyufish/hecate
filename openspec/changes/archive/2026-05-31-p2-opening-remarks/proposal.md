## Why — 动机

当用户与 Agent 开始新的对话时，他们看到一个空白输入框，没有任何关于 Agent 能做什么的指引。每次回复后，用户必须自己想出后续问题。这造成了使用摩擦并降低了参与度。主流 Agent 平台（Coze、AgentArts）通过自动生成开场问候语和上下文相关的后续建议来解决这一问题——使对话从一开始就感觉自然且有引导。

## What Changes — 变更内容

- **开场白生成**：当对话开始时（第一条用户消息或显式请求），系统使用 Agent 的角色设定和配置自动生成问候消息和建议的起始问题。
- **后续建议**：每次助手回复后，系统根据对话历史和 Agent 能力生成 3-5 个上下文相关的后续问题建议。
- **SSE 流式事件**：在 SSE 流中新增 `"suggestions"` 事件类型，在内容之后、`[DONE]` 之前发出。
- **API 响应扩展**：非流式响应在现有 `annotations` 旁包含 `suggested_questions` 字段。
- **Agent 配置**：`AgentModel` 上新增可选的 `opening_remarks`（静态问候覆盖）和 `enable_suggestions`（开关，默认 true）字段。
- **基于 LLM 的生成**：使用轻量级 LLM 调用生成上下文感知的建议，并回退到从 Agent 角色设定派生的静态列表。

## Capabilities — 能力

### New Capabilities — 新增能力
- `opening-remarks`：开场问候生成和后续问题建议系统 — 由 LLM 驱动并带有静态回退、SSE 事件流和 Agent 级配置

### Modified Capabilities — 修改的能力
- `context-assembler`：添加对建议生成提示模式的支持，与现有的知识注入并存

## Impact — 影响范围

- **Models**：`AgentModel` 通过 Alembic 迁移新增 `opening_remarks`（str | None）和 `enable_suggestions`（bool，默认 True）列
- **Services**：`ConversationService` 新增 `_generate_opening()` 和 `_generate_suggestions()` 方法
- **API**：`ChatCompletionRequest` 新增 `generate_opening`（bool）和 `generate_suggestions`（bool）标志；`ChatMessage` 新增 `suggested_questions` 字段
- **API**：在 `[DONE]` 之前发送新的 SSE 事件类型 `{"type": "suggestions", "questions": [...]}`
- **Streaming**：在主回复完成后，通过辅助 LLM 调用生成建议
- **Dependencies**：无新的外部依赖 — 使用现有的 LLM 服务
