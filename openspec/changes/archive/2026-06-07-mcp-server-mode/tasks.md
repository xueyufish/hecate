## 1. 依赖与配置

- [x] 1.1 将 `fastmcp` 添加到 `pyproject.toml` 基础依赖（最新稳定版）
- [x] 1.2 将 `mcp`（官方 SDK）添加到 `pyproject.toml` 基础依赖
- [x] 1.3 向 `core/config.py` 添加 MCP 配置设置：`MCP_SERVER_ENABLED`、`MCP_SERVER_HOST`、`MCP_SERVER_PORT`、`MCP_AUTH_TYPE`、`MCP_TRANSPORT`、`MCP_CLIENT_TIMEOUT`
- [x] 1.4 用所有新的 MCP 设置和注释更新 `.env.example`
- [x] 1.5 安装新依赖：`uv pip install -e ".[dev]"`

## 2. MCP Client — 用真实 SDK 替换 Mock

- [x] 2.1 重写 `services/mcp/client.py` — 使用 `mcp` SDK 的 `ClientSession` 创建 `HecateMCPClient` 类，支持 `streamable_http_client` 和 `stdio_client`
- [x] 2.2 创建 `services/mcp/connection.py` — `MCPClientManager`，管理多个服务器连接、工具发现和调用路由
- [x] 2.3 更新 `services/mcp/sync.py` — `MCPToolSync` 使用真实的 `HecateMCPClient.list_tools()` 替代 mock 数据
- [x] 2.4 接入 `services/tool/registry.py` — 将 `source="mcp"` 的 `NotImplementedError` 替换为通过 `MCPClientManager.call_tool()` 路由
- [x] 2.5 移除模块级单例（`mcp_manager`、`mcp_tool_sync`）— 使用工厂/惰性初始化模式

## 3. MCP Server — 核心基础设施

- [x] 3.1 创建 `services/mcp/server.py` — `create_mcp_server()` 工厂函数，构建带 Streamable HTTP 传输的 `FastMCP("hecate-mcp-server")`
- [x] 3.2 创建 `services/mcp/auth.py` — `verify_mcp_auth(ctx: Context)` 辅助函数，根据 `MCP_AUTH_TYPE` 从 MCP 请求标头验证 API key 或 JWT
- [x] 3.3 创建 `services/mcp/session_manager.py` — `MCPSessionManager`，将 MCP 会话映射到 Hecate `SessionModel`，处理 `session_create` 和会话查找
- [x] 3.4 在 `main.py` 中将 MCP Server 挂载到 FastAPI 应用 — 当 `MCP_SERVER_ENABLED=true` 时条件挂载到 `/mcp`，合并生命周期

## 4. MCP Server — Agent 运行时工具

- [x] 4.1 实现 `session_create(agent_id: str)` MCP 工具 — 创建 `SessionModel`，返回 session_id
- [x] 4.2 实现 `agent_chat(session_id: str, message: str)` MCP 工具 — 使用会话上下文调用 `WorkflowExecutionService.execute()`，返回响应
- [x] 4.3 实现 `session_list(agent_id: str | None)` MCP 工具 — 带分页列出活动会话
- [x] 4.4 实现 `session_resume(session_id: str, message: str)` MCP 工具 — 恢复中断的会话
- [x] 4.5 实现 `conversation_history(conversation_id: str)` MCP 工具 — 检索消息历史

## 5. MCP Server — Agent CRUD 工具

- [x] 5.1 实现 `agent_list(workspace_id: str | None)` MCP 工具 — 带分页查询 AgentModel
- [x] 5.2 实现 `agent_create(name, persona, model_config, mode, tools, knowledge_base_ids)` MCP 工具 — 创建 AgentModel
- [x] 5.3 实现 `agent_update(agent_id, **fields)` MCP 工具 — 更新 AgentModel 字段
- [x] 5.4 实现 `agent_delete(agent_id)` MCP 工具 — 软删除 agent

## 6. MCP Server — 知识库工具

- [x] 6.1 实现 `knowledge_list()` MCP 工具 — 列出所有知识库
- [x] 6.2 实现 `knowledge_search(kb_id, query, limit, mode)` MCP 工具 — 调用 `KnowledgeBaseService.search()`
- [x] 6.3 实现 `knowledge_create(name, description, embedding_model, chunk_strategy)` MCP 工具 — 创建 KnowledgeBaseModel + 集合
- [x] 6.4 实现 `knowledge_ingest(kb_id, content, metadata)` MCP 工具 — 调用 `KnowledgeBaseService.ingest_document_text()`

## 7. MCP Server — 工具执行工具

- [x] 7.1 实现 `tool_list(source: str | None)` MCP 工具 — 带可选源过滤器查询 ToolModel
- [x] 7.2 实现 `tool_execute(tool_name, arguments)` MCP 工具 — 调用 `ToolRegistry.execute()`
- [x] 7.3 实现 `tool_create(name, description, parameters, source)` MCP 工具 — 创建 ToolModel

## 8. MCP Server — 资源与 Prompts

- [x] 8.1 实现 `agent://list` MCP 资源 — 以结构化 JSON 形式返回 agent 目录
- [x] 8.2 实现 `knowledge://list` MCP 资源 — 以结构化 JSON 形式返回 KB 目录
- [x] 8.3 实现 `tool://list` MCP 资源 — 以结构化 JSON 形式返回工具目录
- [x] 8.4 实现 `system-template://{prompt_id}` MCP prompt — 返回提示模板内容

## 9. 测试

- [x] 9.1 创建 `tests/test_services/test_mcp/` 目录及 `__init__.py`
- [x] 9.2 测试 `HecateMCPClient` — 使用 mock MCP server 的连接、list_tools、call_tool、disconnect
- [x] 9.3 测试 `MCPClientManager` — add_server、discover_tools、call_tool 路由、disconnect_all
- [x] 9.4 使用 mock DB session 测试 MCP Server 工具 — agent_list、agent_create、agent_chat、knowledge_search
- [x] 9.5 测试 MCP Server 资源 — agent://list、knowledge://list、tool://list
- [x] 9.6 测试 MCP 认证 — 使用有效/无效/缺失 API key 的 verify_mcp_auth
- [x] 9.7 测试 `MCPSessionManager` — session_create、会话查找、无效会话错误
- [x] 9.8 测试 ToolRegistry MCP 路由 — 验证 mcp 工具通过 MCPClientManager 路由
- [x] 9.9 测试 MCP Server 挂载 — 验证启用时 /mcp 端点存在，禁用时不存在

## 10. 验证

- [x] 10.1 运行 `ruff check src/hecate/ tests/` — 零错误
- [x] 10.2 运行 `ruff format --check src/ tests/` — 零错误
- [x] 10.3 运行 `mypy src/` — 零错误
- [x] 10.4 运行 `python -m pytest tests/ -q` — 所有测试通过（现有 + 新增）
