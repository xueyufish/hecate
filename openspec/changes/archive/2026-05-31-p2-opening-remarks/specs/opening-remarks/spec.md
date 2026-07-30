## ADDED Requirements — 新增需求

### Requirement: Opening remarks generation — 需求：开场白生成
系统应在对话开始时生成包含 3 个起始问题的开场问候。问候应基于 Agent 的角色设定、工具和知识库。当 Agent 配置了 `opening_remarks` 字段时，系统应使用该静态文本而非通过 LLM 生成。

#### Scenario: Auto-generated opening remarks for new conversation — 场景：为新对话自动生成开场白
- **WHEN** `generate_opening=true` 在聊天请求中设置，且 messages 数组恰好包含 1 条 role 为 "user" 的消息
- **THEN** 系统应返回包含问候语和 3 个与 Agent 角色设定和能力相关的起始问题的助手回复

#### Scenario: Static opening remarks override — 场景：静态开场白覆盖
- **WHEN** `generate_opening=true` 且 Agent 有非空的 `opening_remarks` 字段
- **THEN** 系统应返回静态 `opening_remarks` 文本作为问候语，附带 3 个基于静态文本的 LLM 生成的起始问题

#### Scenario: Opening remarks disabled — 场景：开场白已禁用
- **WHEN** 请求中 `generate_opening` 为 false 或未提供
- **THEN** 系统不应生成开场白，正常进行聊天

#### Scenario: Opening remarks for agent without persona — 场景：没有角色设定的 Agent 的开场白
- **WHEN** `generate_opening=true` 且 Agent 没有配置角色设定
- **THEN** 系统应生成通用问候语（"Hi! How can I help you today?"）及 3 个通用起始问题

### Requirement: Follow-up question suggestions — 需求：后续问题建议
系统应在每次助手回复后，在 `generate_suggestions=true` 时生成 3-5 个上下文相关的后续问题。建议应基于对话历史（最近 2 轮）和 Agent 的角色设定。系统应使用辅助 LLM 调用进行生成，带 2 秒超时和静态回退。

#### Scenario: Suggestions generated after response — 场景：回复后生成建议
- **WHEN** 聊天请求中设置了 `generate_suggestions=true` 且助手已回复
- **THEN** 系统应返回 3-5 个与对话上下文相关的后续问题建议

#### Scenario: LLM suggestion generation fails — 场景：LLM 建议生成失败
- **WHEN** 建议 LLM 调用失败或超时（2 秒）
- **THEN** 系统应回退到返回从 Agent 角色设定关键词派生的 3 个通用问题

#### Scenario: Suggestions disabled per agent — 场景：按 Agent 禁用建议
- **WHEN** Agent 设置了 `enable_suggestions=false`
- **THEN** 无论请求标志如何，系统都不应生成建议

#### Scenario: Suggestions disabled per request — 场景：按请求禁用建议
- **WHEN** 请求中 `generate_suggestions` 为 false 或未提供
- **THEN** 系统不应生成建议

### Requirement: Suggestions in streaming responses — 需求：流式响应中的建议
系统应在内容流式传输完成后、`done` 事件之前，将后续建议作为类型化 SSE 事件 `{"type": "suggestions", "questions": [...]}` 发出。对于开场白，建议应包含在同一事件中。

#### Scenario: Suggestions streamed after content — 场景：内容后流式传输建议
- **WHEN** 流式已启用且 `generate_suggestions=true`
- **THEN** 系统应在所有内容事件之后、`done` 事件之前发送 `{"type": "suggestions", "questions": ["q1", "q2", ...]}`

#### Scenario: Opening remarks with suggestions streamed — 场景：开场白带建议流式传输
- **WHEN** 流式已启用且 `generate_opening=true`
- **THEN** 系统应发送问候语作为内容事件，然后发送带 3 个起始问题的建议事件，最后发送 `done` 事件

### Requirement: Suggestions in non-streaming responses — 需求：非流式响应中的建议
系统应在非流式响应中，在助手 `ChatMessage` 的 `suggested_questions` 字段中包含后续建议。对于开场白，问候语应为消息内容，起始问题在 `suggested_questions` 中。

#### Scenario: Suggestions in non-streaming response — 场景：非流式响应中的建议
- **WHEN** 流式已禁用且 `generate_suggestions=true`
- **THEN** 响应消息应在内容旁包含 `suggested_questions: ["q1", "q2", ...]`

#### Scenario: Opening remarks in non-streaming response — 场景：非流式响应中的开场白
- **WHEN** 流式已禁用且 `generate_opening=true`
- **THEN** 响应消息应包含问候语作为内容，`suggested_questions` 包含 3 个起始问题

### Requirement: Agent configuration for suggestions — 需求：建议的 Agent 配置
`AgentModel` 应支持 `opening_remarks`（TEXT，可为空）用于静态问候覆盖，以及 `enable_suggestions`（BOOLEAN，默认 true）用于在 Agent 级别切换建议生成。

#### Scenario: Agent with static opening remarks — 场景：带静态开场白的 Agent
- **WHEN** Agent 以 `opening_remarks="Welcome! I'm your assistant."` 创建
- **THEN** Agent 模型应存储该文本，并在请求开场白时作为问候语使用

#### Scenario: Agent with suggestions disabled — 场景：建议被禁用的 Agent
- **WHEN** Agent 以 `enable_suggestions=false` 创建
- **THEN** 无论请求标志如何，都不应为与该 Agent 的任何对话生成建议

### Requirement: Suggestion prompt template — 需求：建议提示模板
系统应使用结构化提示模板生成建议。模板应包括：Agent 角色设定（最多 200 字符）、最近 2 轮对话内容和当前回复。提示应指示 LLM 返回包含 3-5 个简洁问题的 JSON 数组。

#### Scenario: Prompt includes relevant context — 场景：提示包含相关上下文
- **WHEN** 为关于"数据库优化"的对话生成建议
- **THEN** 提示应包括 Agent 的角色设定、用户关于"数据库优化"的问题以及助手关于索引策略的回复

#### Scenario: Prompt handles long personas — 场景：提示处理长角色设定
- **WHEN** Agent 的角色设定超过 200 字符
- **THEN** 模板应将角色设定截断至 200 字符，并以"..."结尾
