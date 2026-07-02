## ADDED Requirements

### Requirement: LiteLLM 封装层

系统 MUST 通过 LiteLLM 库实现模型路由，支持 100+ LLM Provider。模型调用 SHALL 通过统一的 `llm_invoke(messages, config)` 接口发起。LiteLLM 配置 MUST 从环境变量或配置文件加载 Provider 的 API Key 和 base_url。系统 MUST 支持 OpenAI、Anthropic、Azure OpenAI 等主流 Provider 作为 P1 验证目标。

#### Scenario: 调用 OpenAI GPT-4o 模型
- **WHEN** 通过 `llm_invoke` 调用，config.model="gpt-4o"
- **THEN** LiteLLM SHALL 将请求路由到 OpenAI API，返回 LLM 响应

#### Scenario: Provider API Key 未配置时拒绝调用
- **WHEN** 尝试调用 Anthropic Claude 模型但未配置 ANTHROPIC_API_KEY
- **THEN** MUST 抛出 `ModelRoutingError`，提示对应 Provider 的 API Key 缺失

### Requirement: Streaming 响应

模型路由 MUST 支持 SSE（Server-Sent Events）格式的流式响应。当调用方请求 stream=true 时，`llm_invoke` SHALL 返回异步生成器，逐个 yield Token 片段。每个 Token 片段 MUST 遵循 OpenAI `ChatCompletionChunk` 的 delta 格式。流式传输过程中发生错误时，MUST 发送包含错误信息的 SSE 事件后关闭连接。

#### Scenario: 流式接收 LLM Token
- **WHEN** 调用 `llm_invoke(messages, config)` 且 config.stream=True
- **THEN** MUST 返回 AsyncGenerator，每次 yield `{"choices": [{"delta": {"content": "token_text"}}]}` 格式的 chunk

#### Scenario: 流式传输中 Provider 返回错误
- **WHEN** 流式传输第 5 个 Token 时 Provider 返回 rate limit 错误
- **THEN** MUST 发送 `data: {"error": {"code": "RATE_LIMITED", "message": "..."}}` SSE 事件后关闭流

### Requirement: Tool Calling 协议

系统 MUST 实现完整的 OpenAI 兼容 Tool Calling 协议。调用 LLM 时 MUST 支持传入 `tools` 参数（函数定义列表，JSON Schema 格式）。LLM 返回 `tool_calls` 时，系统 SHALL 解析出工具名称和参数，执行对应工具，将结果以 `role=tool` 消息回注到对话上下文中，继续调用 LLM 直到不再返回 tool_calls。

#### Scenario: LLM 请求调用工具并获取结果
- **WHEN** LLM 返回 `tool_calls: [{"id": "call_123", "function": {"name": "web_search", "arguments": "{\"query\": \"Hecate\"}"}}]`
- **THEN** 系统 MUST 解析出工具名 "web_search" 和参数，执行工具，将结果以 `{"role": "tool", "tool_call_id": "call_123", "content": "搜索结果..."}` 回注

#### Scenario: 多轮 Tool Calling 循环
- **WHEN** LLM 第一次返回 tool_call，工具执行后回注结果，LLM 第二次又返回新的 tool_call
- **THEN** 系统 MUST 继续执行第二轮工具调用并回注，直到 LLM 返回纯文本回复（无 tool_calls）

### Requirement: 模型降级策略

系统 MUST 支持模型降级（Fallback）策略。Agent 的 `model_config` 可指定 `fallback_model`。当主模型调用失败（网络错误、限流、超时）时，系统 SHALL 自动切换到 fallback_model 重试。降级发生时 MUST 在响应 metadata 中记录 `fallback_used=True` 和 `original_model`。

#### Scenario: 主模型限流自动降级
- **WHEN** 主模型 "gpt-4o" 返回 HTTP 429 限流错误，且 fallback_model 配置为 "gpt-4o-mini"
- **THEN** 系统 MUST 自动使用 "gpt-4o-mini" 重试相同请求，响应 metadata 包含 `{"fallback_used": true, "original_model": "gpt-4o"}`

#### Scenario: 主模型和备用模型均失败
- **WHEN** 主模型和 fallback_model 均调用失败
- **THEN** 系统 MUST 返回 `ModelRoutingError`，包含两个模型的错误信息

### Requirement: Provider 配置管理

系统 SHALL 支持通过环境变量或 YAML 配置文件管理 LLM Provider 配置。配置 MUST 包含每个 Provider 的 `api_key`、`base_url`（可选）、`api_version`（可选）、速率限制参数。系统 MUST 在启动时验证所有已配置 Provider 的连通性。

#### Scenario: 从环境变量加载多 Provider 配置
- **WHEN** 环境变量设置 `OPENAI_API_KEY=sk-xxx` 和 `ANTHROPIC_API_KEY=sk-ant-xxx`
- **THEN** 系统启动时 MUST 加载两个 Provider 配置，`GET /v1/models` 返回两个 Provider 的可用模型

#### Scenario: Provider 连通性验证
- **WHEN** 系统启动时检测到配置了无效的 API Key
- **THEN** MUST 在日志中输出警告信息，但不阻止启动，仅在实际调用时返回错误
