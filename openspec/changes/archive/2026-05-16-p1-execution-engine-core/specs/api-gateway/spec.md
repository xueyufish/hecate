## ADDED Requirements

### Requirement: FastAPI 应用初始化

系统 MUST 基于 FastAPI 创建 API 应用。应用 SHALL 配置 CORS 中间件（允许所有来源的 GET/POST/PUT/DELETE 方法）。应用 MUST 注册以下路由前缀：`/v1`（OpenAI 兼容层）、`/api`（Hecate 管理 API）。应用 SHALL 在启动时初始化数据库连接池和外部服务客户端。

#### Scenario: 应用启动健康检查
- **WHEN** 发送 `GET /health` 请求
- **THEN** MUST 返回 HTTP 200，body 为 `{"status": "ok"}`

### Requirement: OpenAI 兼容接口

系统 MUST 实现 `/v1/chat/completions` 端点，兼容 OpenAI Chat Completions API 格式。请求体 MUST 包含 `model`、`messages` 字段，可选 `stream`、`temperature`、`tools`、`tool_choice` 等参数。响应 MUST 遵循 OpenAI 的 `ChatCompletion` 格式（包含 id、object、choices、usage）。`stream=true` 时 MUST 返回 SSE 格式的 `ChatCompletionChunk` 流。系统 MUST 实现 `/v1/models` 端点，返回当前可用的模型列表。

#### Scenario: 非流式对话请求
- **WHEN** 发送 `POST /v1/chat/completions`，body 为 `{"model": "gpt-4o", "messages": [{"role": "user", "content": "你好"}]}`
- **THEN** MUST 返回 HTTP 200，响应体包含 `choices[0].message.content`，格式严格兼容 OpenAI

#### Scenario: 流式对话 SSE 输出
- **WHEN** 发送 `POST /v1/chat/completions`，body 包含 `stream: true`
- **THEN** MUST 返回 HTTP 200，Content-Type 为 `text/event-stream`，每个 chunk 为 `data: {json}\n\n` 格式，最终以 `data: [DONE]\n\n` 结束

#### Scenario: 查询可用模型列表
- **WHEN** 发送 `GET /v1/models`
- **THEN** MUST 返回 HTTP 200，body 为 `{"object": "list", "data": [{"id": "gpt-4o", "object": "model", ...}]}` 格式

### Requirement: Agent 管理 API

系统 MUST 实现 `/api/agents` 的 CRUD 操作。`POST /api/agents` 创建 Agent，请求体包含 name、persona、model_config、mode、tools 等字段。`GET /api/agents` 列出所有 Agent（支持分页参数 `page` 和 `page_size`）。`GET /api/agents/{id}` 获取单个 Agent 详情。`PUT /api/agents/{id}` 更新 Agent 配置。`DELETE /api/agents/{id}` 软删除 Agent。

#### Scenario: 创建并查询 Agent
- **WHEN** 发送 `POST /api/agents` 创建 Agent，然后发送 `GET /api/agents/{id}`
- **THEN** 创建时 MUST 返回 HTTP 201 和完整 Agent 对象，查询时 MUST 返回相同的 Agent 数据

#### Scenario: 分页列出 Agent
- **WHEN** 发送 `GET /api/agents?page=1&page_size=10`
- **THEN** MUST 返回 HTTP 200，body 包含 `items`（Agent 数组）和 `total`（总数），items 长度不超过 page_size

### Requirement: Session 和 Tool 管理 API

系统 MUST 实现 `/api/sessions` 端点：`POST /api/sessions` 创建 Session（需指定 agent_id），`GET /api/sessions` 列出 Session，`GET /api/sessions/{id}` 获取 Session 详情，`POST /api/sessions/{id}/resume` 恢复中断的 Session。系统 MUST 实现 `/api/tools` 端点：`GET /api/tools` 列出所有工具（包含 builtin + custom + mcp），`GET /api/tools/{id}` 获取工具详情。

#### Scenario: 创建 Session 并发起对话
- **WHEN** 发送 `POST /api/sessions` body 为 `{"agent_id": "uuid-xxx"}`
- **THEN** MUST 返回 HTTP 201，body 包含 session_id、status="active"、created_at

#### Scenario: 恢复中断的 Session
- **WHEN** 发送 `POST /api/sessions/{id}/resume` body 为 `{"resume_value": "approved"}`
- **THEN** MUST 返回 HTTP 200，引擎从断点继续执行

### Requirement: Knowledge Base 和 Skill 管理 API

系统 MUST 实现 `/api/knowledge-bases` 端点：`POST /api/knowledge-bases` 创建知识库、`GET /api/knowledge-bases` 列出知识库、`POST /api/knowledge-bases/{id}/documents` 上传文档、`GET /api/knowledge-bases/{id}/documents` 列出文档及解析状态。系统 MUST 实现 `/api/skills` 端点：`GET /api/skills` 列出所有已发现的 Skill、`GET /api/skills/{id}` 获取 Skill 详情。

#### Scenario: 创建知识库并上传文档
- **WHEN** 发送 `POST /api/knowledge-bases` 创建知识库，然后 `POST /api/knowledge-bases/{id}/documents` 上传 PDF 文件
- **THEN** 创建时 MUST 返回知识库信息，上传时 MUST 返回文档记录（parsing_status="pending"）并触发异步解析

### Requirement: API Key 认证

所有 API 请求 MUST 通过 `Authorization: Bearer <api_key>` 头进行认证。系统 SHALL 从环境变量或配置文件加载有效的 API Key 列表。请求缺少 Authorization 头或 API Key 无效时，MUST 返回 HTTP 401，body 为统一错误格式。`/health` 端点 MUST 豁免认证。

#### Scenario: 有效 API Key 通过认证
- **WHEN** 请求头包含 `Authorization: Bearer valid-key-123`
- **THEN** MUST 正常处理请求，返回对应响应

#### Scenario: 无效 API Key 被拒绝
- **WHEN** 请求头包含 `Authorization: Bearer invalid-key`
- **THEN** MUST 返回 HTTP 401，body 为 `{"error": {"code": "UNAUTHORIZED", "message": "Invalid API key"}}`

### Requirement: Rate Limiting

系统 MUST 实现基于 API Key 的请求速率限制。默认限制 SHALL 为每分钟 60 次请求。超过限制时 MUST 返回 HTTP 429，body 为统一错误格式，响应头 MUST 包含 `Retry-After`。

#### Scenario: 超出速率限制
- **WHEN** 同一 API Key 在 1 分钟内发送第 61 次请求
- **THEN** MUST 返回 HTTP 429，响应头包含 `Retry-After: <秒数>`

### Requirement: 统一错误格式

所有 API 错误响应 MUST 使用统一 JSON 格式：`{"error": {"code": "ERROR_CODE", "message": "人类可读的错误描述", "details": null}}`。`code` MUST 为大写蛇形命名（如 `VALIDATION_ERROR`、`NOT_FOUND`、`INTERNAL_ERROR`）。HTTP 状态码 MUST 与错误类型对应（400/401/404/422/429/500）。

#### Scenario: 资源不存在错误
- **WHEN** 请求 `GET /api/agents/{不存在的id}`
- **THEN** MUST 返回 HTTP 404，body 为 `{"error": {"code": "NOT_FOUND", "message": "Agent not found"}}`
