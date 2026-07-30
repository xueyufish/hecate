## ADDED Requirements — 新增需求

### Requirement: LiteLLM 封装层 — LiteLLM Wrapper

系统 MUST 通过 LiteLLM 实现模型路由，支持 100+ Provider。统一通过 `llm_invoke(messages, config)` 接口调用。支持 OpenAI、Anthropic、Azure OpenAI 等主流 Provider。
— System MUST route models via LiteLLM supporting 100+ providers through unified `llm_invoke()` interface.

#### Scenario: Provider API Key 未配置时拒绝调用 — Reject when Provider API Key missing
- **WHEN** 调用 Anthropic Claude 但未配置 ANTHROPIC_API_KEY
- **THEN** MUST 抛出 `ModelRoutingError`

### Requirement: Streaming 响应 — Streaming Response

模型路由 MUST 支持 SSE 流式响应。`llm_invoke` 返回异步生成器，逐个 yield Token 片段。流式传输中发生错误时发送错误 SSE 事件后关闭。
— Model routing MUST support SSE streaming via async generator.

#### Scenario: 流式传输中 Provider 返回错误 — Provider error during streaming
- **WHEN** 流式传输第 5 个 Token 时 Provider 返回 rate limit 错误
- **THEN** MUST 发送错误 SSE 事件后关闭流

### Requirement: Tool Calling 协议 — Tool Calling Protocol

系统 MUST 实现 OpenAI 兼容 Tool Calling。支持 `tools` 参数传入函数定义，解析 `tool_calls`，执行工具，结果以 `role=tool` 回注，循环直到无 tool_calls。
— System MUST implement OpenAI-compatible Tool Calling protocol with automatic tool execution loop.

#### Scenario: 多轮 Tool Calling 循环 — Multi-round Tool Calling loop
- **WHEN** LLM 连续返回 tool_calls 两次
- **THEN** 系统 MUST 继续执行工具调用直到 LLM 返回纯文本回复

### Requirement: 模型降级策略 — Model Fallback Strategy

Agent 的 `model_config` 支持 `fallback_model`。主模型失败时自动切换到 fallback_model 重试。响应 metadata 记录 `fallback_used=True` 和 `original_model`。
— Agent supports fallback_model. Auto-switch on failure, record fallback in response metadata.

#### Scenario: 主模型限流自动降级 — Auto-fallback on rate limit
- **WHEN** 主模型 "gpt-4o" 返回 429，fallback_model 为 "gpt-4o-mini"
- **THEN** 系统 MUST 自动用 "gpt-4o-mini" 重试

#### Scenario: 主模型和备用模型均失败 — Both primary and fallback fail
- **WHEN** 主模型和 fallback_model 均调用失败
- **THEN** 系统 MUST 返回 `ModelRoutingError`

### Requirement: Provider 配置管理 — Provider Configuration Management

系统 SHALL 支持通过环境变量或 YAML 配置文件管理 Provider 配置。启动时验证连通性。
— System SHALL support environment variable or YAML config for Provider management.

#### Scenario: 从环境变量加载多 Provider 配置 — Load multiple Provider configs from env
- **WHEN** 环境变量设置多个 API Key
- **THEN** 启动时加载所有配置，`/v1/models` 返回所有可用模型
