## Context — 背景

Hecate 是一个基于 FastAPI 构建的"MCP-first Agent 平台"。MCP Client（feature 5.3）以数据模型和 API 桩的形式存在，但使用了 mock 实现——没有真正的 MCP SDK 集成。根本没有 MCP Server。该平台需要双向 MCP：消费外部工具（Client）和暴露内部能力（Server）。

当前状态：
- `services/mcp/client.py` — `MCPClient` 返回 mock 数据（`{"result": "Mock result", "success": True}`）
- `services/mcp/sync.py` — `MCPToolSync` 转换格式但从不调用真实服务器
- `services/tool/registry.py` — MCP 工具路由抛出 `NotImplementedError`
- `pyproject.toml` 中没有 `fastmcp` 或 `mcp` SDK
- `main.py` 中的 FastAPI 应用有 15+ 个管理路由器 + OpenAI 兼容的 `/v1` 端点
- 现有服务层：`WorkflowExecutionService`、`KnowledgeBaseService`、`ToolRegistry`、`AgentExecutionPort`、`LLMService`、`SessionModel`

研究发现：
- Google ADK 官方示例使用 `fastmcp` 构建 MCP 服务器
- `fastmcp` 提供原生 FastAPI 集成：`FastMCP.from_fastapi(app)`、`mcp.http_app()`、`app.mount()`
- MCP 协议已弃用 HTTP+SSE（2024-11-05），推荐使用 Streamable HTTP（2025-03-26+）
- `fastmcp.run(transport="http")` 原生支持 Streamable HTTP
- 主要平台（Salesforce、Google、Microsoft）都支持 Streamable HTTP 作为主要传输方式

## Goals / Non-Goals — 目标 / 非目标

**目标：**

- 使用 `fastmcp` 构建 MCP Server，将 Hecate 的完整能力面暴露为 MCP 工具、资源和 prompts
- 服务端工具涵盖运行时操作（agent 执行、知识搜索、工具调用、session 管理）和 CRUD 操作（创建/列出/更新/删除 agents、KBs、tools）
- 通过 ASGI 挂载将 MCP Server 集成到现有 FastAPI 应用的 `/mcp` 路径——共享数据库会话、共享认证
- 修复 MCP Client 以使用真实的 `mcp` SDK，支持 Streamable HTTP + stdio 传输
- 通过真实客户端接入 ToolRegistry MCP 路由
- 会话管理：MCP 客户端通过 `session_create(agent_id)` 工具创建 Hecate sessions，然后使用 `agent_chat(session_id, message)` 进行有状态对话（方案 C）
- 认证：通过 MCP 请求标头复用 `HECATE_API_KEYS`；通过 `MCP_AUTH_TYPE` 配置

**非目标：**

- MCP Sampling 能力（P3——让外部客户端通过 Hecate 请求 LLM 补全）
- MCP Gateway / API-to-MCP 自动转换（feature 5.4a，P3）
- MCP Sandbox 安全（feature 5.12，P3）
- MCP 的 OAuth2/OIDC 认证（未来增强）
- Server 的 stdio 传输（平台仅使用 SSE/Streamable HTTP）
- 所有 REST 端点的自动 `FastMCP.from_fastapi()` 转换（我们将手工制作工具以更好地控制）

## Decisions — 设计决策

### D1: SDK — Server 使用 `fastmcp`，Client 使用 `mcp` SDK

**Server**：使用 `fastmcp`（由 jlowin/Community 维护）。理由：
- 原生 FastAPI 集成（`http_app()`、`mount()`、`combine_lifespans()`）
- `@mcp.tool` / `@mcp.resource` 装饰器 API——比原始 `@app.call_tool()` 处理器更简洁
- Google ADK 官方 Codelabs 使用 `fastmcp` 构建 MCP 服务器
- 开箱即用的 Streamable HTTP（`transport="http"`）
- 被 openJiuwen（我们的参考平台）使用

**Client**：使用官方 `mcp` Python SDK（`modelcontextprotocol/python-sdk`）。理由：
- 官方 Anthropic SDK——稳定，规范完整
- 支持 Streamable HTTP、SSE 和 stdio 传输
- `ClientSession` 类提供完整的协议生命周期管理
- `fastmcp` 客户端更高级但生产使用灵活度较低

考虑的替代方案：
- 两者都使用 `mcp` SDK → Server 端更多样板代码，没有 FastAPI 集成
- 两者都使用 `fastmcp` → 客户端 API 不如官方 SDK 成熟

### D2: 传输方式 — 仅 Streamable HTTP

使用 Streamable HTTP（`transport="http"`）作为 MCP Server 的唯一传输方式。

理由：
- HTTP+SSE 传输自协议版本 2025-03-26 起已弃用
- Streamable HTTP 是当前标准——单个端点，支持 JSON 和 SSE 响应
- 支持无状态服务器——更好的基础设施兼容性
- 所有主要客户端（Claude Code、Cursor、VS Code、Google ADK）现在都支持 Streamable HTTP

对于 MCP Client：支持 Streamable HTTP 和 stdio（stdio 用于本地子进程工具）。

### D3: FastAPI 集成 — 在 `/mcp` 进行 ASGI 挂载

```python
# services/mcp/server.py
mcp = FastMCP("hecate-mcp-server")

# main.py
mcp_app = mcp.http_app(path="/mcp")
app.mount("/mcp", mcp_app)
```

将 MCP Server ASGI 应用挂载到现有 FastAPI 应用的 `/mcp` 路径。共享：
- 数据库会话（工具通过 `async_session_factory` 创建自己的 `AsyncSession`）
- 认证（工具函数中的 API 密钥验证）
- 生命周期（通过 `combine_lifespans` 合并）

考虑的替代方案：通过 uvicorn 使用独立端口 → 不必要的运维复杂性，无共享状态。

### D4: 会话管理 — 方案 C（两步有状态模式）

MCP 工具调用本质上是无状态的，但 agent 对话是有状态的。设计：

1. `session_create(agent_id)` → 返回 `session_id`（创建 Hecate `SessionModel`）
2. `agent_chat(session_id, message)` → 向现有会话发送消息（调用 `WorkflowExecutionService`）

这匹配 Hecate 现有的会话架构（`POST /api/sessions` + `POST /v1/chat/completions?session_id=...`）。

### D5: 工具面 — 完整能力暴露

运行时工具：
- `agent_list`、`agent_chat`、`agent_create`、`agent_update`、`agent_delete`
- `knowledge_list`、`knowledge_search`、`knowledge_create`、`knowledge_ingest`
- `tool_list`、`tool_execute`、`tool_create`
- `session_create`、`session_list`、`session_resume`
- `conversation_history`

资源：
- `agent://{agent_id}` — Agent 元数据
- `knowledge://list` — KB 目录
- `tool://list` — 工具目录

Prompts：
- `system-template://{prompt_id}` — 系统提示模板

### D6: 认证 — API Key 直通

MCP 工具从传入请求标头验证 `HECATE_API_KEYS`。`fastmcp` Context 对象提供对请求标头的访问。

```python
@mcp.tool
async def agent_chat(session_id: str, message: str, ctx: Context) -> dict:
    api_key = ctx.request_headers.get("x-api-key", "")
    await verify_api_key(api_key)
    ...
```

配置：`MCP_AUTH_TYPE=api_key|jwt|none`（默认：`api_key`）。

### D7: MCP Client 重构 — 用真实 SDK 替换 mock

将 `MCPClient` / `MCPManager` 单例替换为合适的异步客户端：

```python
# services/mcp/client.py（重构后）
class HecateMCPClient:
    async def connect(self, server_url: str, transport: str = "http") -> None
    async def list_tools(self) -> list[ToolDefinition]
    async def call_tool(self, tool_name: str, arguments: dict) -> Any
    async def disconnect(self) -> None
```

使用 `mcp` SDK 的 `ClientSession` + `streamable_http_client` 或 `stdio_client`。

将 `ToolRegistry.execute()` 的 `source="mcp"` 路由通过 `HecateMCPClient`。

## Risks / Trade-offs — 风险与权衡

- **[R1] `fastmcp` 是社区维护的，不是官方的** → 缓解措施：Google ADK 在官方文档中使用它；积极开发；如果停滞，迁移到 `mcp` SDK 是直接的（更底层的 API）
- **[R2] MCP 工具面很大（20+ 工具）** → 缓解措施：工具是现有服务方法的薄包装；`fastmcp` 从类型提示自动生成 schema；每个工具可独立测试
- **[R3] 认证标头直通可能不适用于所有 MCP 客户端** → 缓解措施：对于受信任的内部网络使用 `MCP_AUTH_TYPE=none`；JWT 支持作为后备；在 .env.example 中有文档说明
- **[R4] ASGI 挂载可能与 CORS 中间件冲突** → 缓解措施：`fastmcp` 文档明确解决了这个问题——避免在 MCP 路径上设置应用范围的 CORS；在有 `CORSMiddleware` 的情况下测试
- **[R5] 连接到外部服务器的真实 MCP Client 引入供应链风险** → 缓解措施：ToolModel 中的工具允许列表；`risk_level` 和 `approval_required` 字段已存在；执行沙箱可用（feature 9.4c ✅）
- **[R6] 双 SDK 依赖（`fastmcp` + `mcp`）增加了依赖面** → 缓解措施：两者都是纯 Python、轻量级且积极维护的；`fastmcp` 自身内部依赖 `mcp` SDK
