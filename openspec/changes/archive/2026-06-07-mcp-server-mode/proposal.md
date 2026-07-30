## Why — 动机

Hecate 将自己定位为"MCP-first Agent 平台"，但目前仅消费 MCP 工具（Client，feature 5.3）——而且该客户端是一个带有 mock 桩的实现，没有真正的 SDK 集成。没有 MCP Server 模式，Hecate 无法被外部 AI 工具（Claude Code、Cursor、VS Code、Google ADK agents）发现或使用，限制了其生态覆盖范围。与此同时，每个主要平台——Google ADK（fastmcp）、Salesforce Agentforce（MCP registry）、Dify、Langflow——现在都将 agent 能力暴露为 MCP 工具。MCP Server 模式完成了双向 MCP 架构，是 Sprint 2 基础设施可扩展性链中的最后一项（13.13 ✅ → 3.1.7 ✅ → 5.9a）。

## What Changes — 变更内容

- 添加 `fastmcp` 依赖，构建一个通过 Streamable HTTP 传输将 Hecate 的能力暴露为 MCP 工具、资源和 prompts 的 MCP Server
- MCP Server 工具涵盖运行时操作（agent 执行、知识搜索、工具调用、session 管理）和 CRUD 操作（创建/列出/更新/删除 agents、知识库、工具、sessions）
- 修复现有 MCP Client（feature 5.3）——用使用 `mcp` Python SDK 的真实实现替换 mock `MCPClient`，支持 Streamable HTTP 和 stdio 传输
- 使用 `fastmcp` 的 ASGI 集成将 MCP Server 挂载到现有 FastAPI 应用的 `/mcp` 路径
- 添加配置：`MCP_SERVER_ENABLED`、`MCP_SERVER_HOST`、`MCP_SERVER_PORT`、`MCP_AUTH_TYPE`、`MCP_TRANSPORT`
- 认证：复用现有的 `HECATE_API_KEYS` 用于 MCP 工具访问；支持 JWT Bearer tokens

## Capabilities — 能力变更

### 新增能力

- `mcp-server`: MCP Server 模式——通过 Streamable HTTP 传输，将 Hecate 的完整能力面（agents、知识库、工具、sessions、conversations）暴露为 MCP 工具/资源/prompts，通过 fastmcp 与 FastAPI 集成
- `mcp-client-real`: 真实的 MCP Client——用实际的 `mcp` SDK ClientSession（支持 Streamable HTTP 和 stdio 传输）替换 mock MCPClient/MCPManager，使 Hecate agents 能够消费真实的外部 MCP 工具

### 修改的能力

- `core-infrastructure`: 向 `Settings` 类添加 MCP Server/Client 配置设置（`MCP_SERVER_ENABLED`、`MCP_SERVER_HOST`、`MCP_SERVER_PORT`、`MCP_AUTH_TYPE`、`MCP_TRANSPORT`、`MCP_CLIENT_TIMEOUT`）
- `tool-registry`: 接入 MCP 工具执行路径——当 `source="mcp"` 时，通过真实的 MCP Client 路由 `ToolRegistry.execute()`，而不是抛出 `NotImplementedError`
- `data-models`: 向 ToolModel 添加 `mcp_server_url` 和 `mcp_enabled` 字段以支持 MCP Client 工具注册；相应更新 schemas

## Impact — 影响范围

- **新依赖**: `fastmcp`（最新稳定版）添加到 `pyproject.toml` 基础依赖；`mcp`（官方 SDK）作为 Client 的基础依赖
- **Services 层**: 新建 `services/mcp/server.py`（MCP Server），重构 `services/mcp/client.py`（真实 Client），新建 `services/mcp/session_manager.py`（MCP session → Hecate session 映射）
- **API 层**: MCP 端点通过 ASGI 挂载到现有 FastAPI 应用的 `/mcp` 路径；无需新的 REST 路由
- **配置**: `core/config.py` + `.env.example` 中的 5 个新环境变量
- **Engine 层**: 无变更——MCP Server 在 services 层运行，调用现有服务接口
- **测试**: 新建 `tests/test_services/test_mcp/` 目录，包含服务端工具、客户端集成和会话管理的测试
- **迁移**: 无需 Alembic 迁移（ToolModel 的字段添加是增量的，在现有迁移路径中处理）
