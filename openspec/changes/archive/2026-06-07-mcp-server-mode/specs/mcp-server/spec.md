## ADDED Requirements — 新增需求

### Requirement: MCP Server 将 Hecate 能力暴露为 MCP 工具
系统 SHALL 提供一个使用 `fastmcp` 的 MCP Server，通过 Streamable HTTP 传输将 agent、知识库、工具、session 和 conversation 操作暴露为 MCP 工具，挂载到 FastAPI 应用的 `/mcp` 路径。

#### Scenario: MCP 客户端发现可用工具
- **WHEN** MCP 客户端连接到服务器并调用 `tools/list`
- **THEN** 服务器返回工具列表，包括 `agent_list`、`agent_chat`、`agent_create`、`knowledge_search`、`knowledge_list`、`tool_execute`、`tool_list`、`session_create`、`session_list` 和 `conversation_history`

#### Scenario: MCP 服务器挂载到 FastAPI 应用
- **WHEN** FastAPI 应用以 `MCP_SERVER_ENABLED=true` 启动
- **THEN** MCP Server ASGI 应用被挂载到 `/mcp`，接受 Streamable HTTP 请求

#### Scenario: MCP Server 禁用
- **WHEN** `MCP_SERVER_ENABLED=false`（默认）
- **THEN** 没有 MCP 端点被挂载，应用行为与之前相同

### Requirement: Agent 运行时工具
系统 SHALL 暴露以下 agent 运行时 MCP 工具：
- `agent_chat(session_id: str, message: str)`：向活动会话发送消息，调用 WorkflowExecutionService，返回 agent 响应
- `session_create(agent_id: str)`：为 agent 创建新的 Hecate session，返回 `session_id`
- `session_list(agent_id: str | None)`：列出活动 session，可选按 agent 过滤
- `session_resume(session_id: str, message: str)`：使用新消息恢复中断的 session
- `conversation_history(conversation_id: str)`：检索会话消息历史

#### Scenario: 创建 session 并聊天
- **WHEN** 客户端调用 `session_create(agent_id="<uuid>")`
- **THEN** 服务器创建带 `agent_id` 和 `status="active"` 的 `SessionModel`，并返回 `{"session_id": "<new-uuid>", "status": "active"}`
- **WHEN** 客户端随后调用 `agent_chat(session_id="<new-uuid>", message="Hello")`
- **THEN** 服务器使用会话上下文调用 `WorkflowExecutionService.execute()` 并返回 agent 的文本响应

#### Scenario: 与不存在的 session 聊天
- **WHEN** 客户端调用 `agent_chat(session_id="<invalid>", message="Hello")`
- **THEN** 服务器返回错误：`{"error": "Session not found"}`

### Requirement: Agent CRUD 工具
系统 SHALL 暴露以下 agent 管理 MCP 工具：
- `agent_list(workspace_id: str | None)`：带分页列出 agents
- `agent_create(name: str, persona: str | None, model_config: dict, mode: str, tools: list | None, knowledge_base_ids: list | None)`：创建新 agent
- `agent_update(agent_id: str, **fields)`：更新 agent 字段
- `agent_delete(agent_id: str)`：软删除 agent

#### Scenario: 通过 MCP 工具创建 agent
- **WHEN** 客户端调用 `agent_create(name="Test Agent", model_config={"model": "gpt-4o"}, mode="chat")`
- **THEN** 服务器在数据库中创建 `AgentModel` 并返回 agent 的 UUID 和元数据

#### Scenario: 列出 agents
- **WHEN** 客户端调用 `agent_list()`
- **THEN** 服务器返回所有未删除 agents 的列表，包含 id、name、mode 和 model_config

### Requirement: 知识库工具
系统 SHALL 暴露以下知识库 MCP 工具：
- `knowledge_list()`：列出所有知识库
- `knowledge_search(kb_id: str, query: str, limit: int, mode: str)`：使用稠密/稀疏/混合搜索搜索知识库
- `knowledge_create(name: str, description: str, embedding_model: str, chunk_strategy: str)`：创建新的知识库
- `knowledge_ingest(kb_id: str, content: str, metadata: dict | None)`：将文本内容摄取到知识库

#### Scenario: 搜索知识库
- **WHEN** 客户端调用 `knowledge_search(kb_id="<uuid>", query="machine learning", limit=5, mode="hybrid")`
- **THEN** 服务器调用 `KnowledgeBaseService.search()` 并返回匹配块列表，包含内容、分数和元数据

#### Scenario: 将文本摄取到知识库
- **WHEN** 客户端调用 `knowledge_ingest(kb_id="<uuid>", content="Some document text...")`
- **THEN** 服务器调用 `KnowledgeBaseService.ingest_document_text()` 并返回摄取结果

### Requirement: 工具执行工具
系统 SHALL 暴露以下工具 MCP 工具：
- `tool_list(source: str | None)`：列出已注册的工具，可选按源过滤
- `tool_execute(tool_name: str, arguments: dict)`：按名称执行注册的工具
- `tool_create(name: str, description: str, parameters: dict, source: str)`：注册新工具

#### Scenario: 执行内置工具
- **WHEN** 客户端调用 `tool_execute(tool_name="web_search", arguments={"query": "Python async"})`
- **THEN** 服务器调用 `ToolRegistry.execute()` 并返回工具的结果

#### Scenario: 按源列出工具
- **WHEN** 客户端调用 `tool_list(source="builtin")`
- **THEN** 服务器仅返回内置工具，包含名称、描述和参数

### Requirement: 用于目录发现的 MCP 资源
系统 SHALL 暴露 MCP 资源：
- `agent://list`：以结构化数据返回 agent 目录
- `knowledge://list`：以结构化数据返回知识库目录
- `tool://list`：以结构化数据返回工具目录

#### Scenario: 客户端读取 agent 目录资源
- **WHEN** MCP 客户端使用 URI `agent://list` 调用 `resources/read`
- **THEN** 服务器返回所有 agents 的 JSON 列表，包含 id、name、mode 和 model_config

### Requirement: 用于系统模板的 MCP Prompts
系统 SHALL 暴露 MCP prompts：
- `system-template://{prompt_id}`：按 ID 返回存储的提示模板

#### Scenario: 客户端检索提示模板
- **WHEN** MCP 客户端使用名称 `system-template://<prompt_id>` 调用 `prompts/get`
- **THEN** 服务器从 Prompt CRUD 系统返回提示模板内容

### Requirement: 通过 API Key 的 MCP Server 认证
系统 SHALL 使用 API 密钥认证验证 MCP 工具调用。`MCP_AUTH_TYPE` 设置控制认证模式：`api_key`（默认）、`jwt` 或 `none`。

#### Scenario: 有效 API 密钥
- **WHEN** 客户端在 MCP 请求的 `x-api-key` 标头中包含有效 API 密钥
- **THEN** 工具正常执行

#### Scenario: 无效 API 密钥
- **WHEN** 当 `MCP_AUTH_TYPE=api_key` 时，客户端包含无效 API 密钥或没有密钥
- **THEN** 服务器返回错误响应，工具不执行

### Requirement: MCP Server 配置
系统 SHALL 在 `Settings` 中提供以下配置设置：
- `MCP_SERVER_ENABLED: bool`（默认：`False`）— 启用/禁用 MCP server
- `MCP_SERVER_HOST: str`（默认：`"0.0.0.0"`）— 服务器绑定主机
- `MCP_SERVER_PORT: int`（默认：`8000`）— 服务器绑定端口
- `MCP_AUTH_TYPE: str`（默认：`"api_key"`）— MCP 请求的认证模式
- `MCP_TRANSPORT: str`（默认：`"http"`）— 传输模式（`http` 用于 Streamable HTTP）

#### Scenario: 默认配置禁用 MCP
- **WHEN** 未设置 MCP 相关环境变量
- **THEN** `MCP_SERVER_ENABLED=False` 且 MCP 服务器不启动
