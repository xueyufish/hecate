## Why

MCP 规范于 2026-07-28 发布史上最大修订——核心转无状态（移除 `initialize`/`Mcp-Session-Id`、`_meta` 自描述请求）、强制 `Mcp-Method`/`Mcp-Name` header 路由以支持普通 round-robin 负载均衡、引入 MRTR 取代长连接 elicitation、流式列表支持 `ttlMs`/`cacheScope` 缓存提示、RFC 9207 校验。官方 Python SDK v2.0.0 与 fastmcp 4.0 同日 stable 发布，同一 server 端点自动伺服新旧时代，decorator 风格工具定义 API 保持兼容。

Hecate 当前 MCP 栈为 `fastmcp>=2.0.0` + `mcp>=1.0.0`——客户端早已切换到 `streamablehttp_client`，但服务端的 fastmcp 与官方 SDK 均处于规范切换的中间状态：协议语义、传输层握手、工具调用序列化均停留在 2025-03-26 时代，无法享受新规范的零会话亲和力、header 路由、可缓存列表等能力；且 SDK v1.x 已进入仅安全修复的维护模式，长期依赖 1.x 本身在累积技术债。

5.4b 是 P3 收尾的 4 个剩余项之一（roadmap Sprint 6 已将目标明确为 2026-07-28 规范，并标记"避免按 2025-03-26 实现后二次迁移"）。Hecate 当前为 alpha 未商用，无存量客户端需要兼容，因此按 roadmap 决策直接落地新规范，不为旧时代客户端做兼容层。

## What Changes

- **依赖升级**（`pyproject.toml`）：`fastmcp>=2.0.0` → `>=4.0.0`；`mcp>=1.0.0` → `>=2.0.0`；新增锁步包 `mcp-types>=2.0.0`。
- **Server 端迁移**（`src/hecate/services/mcp/server.py`）：`FastMCP` 类迁移到 fastmcp 4 API（类名变化、挂载方式、auth/setting 字段位置调整）；`main.py:733` 等处的 `http_app(path="/mcp")` 调整为 fastmcp 4 推荐挂载形态（ASGI app + lifespan 合并）。20+ 工具定义保持装饰器风格，业务逻辑零改动。
- **Client 端迁移**（`src/hecate/services/mcp/client.py`，单文件收敛）：`HecateMCPClient` 从 `ClientSession` + `streamablehttp_client`/`stdio_client` + `AsyncExitStack` 的 v1 三层机器，替换为 v2 的 `Client(target)` 自动协商 API。公开接口（`connect` / `connect_stdio` / `list_tools` / `call_tool` / `disconnect` / health check）保持稳定，使 5.4c 连接池/熔断/注册层（`connection.py`）无感。
- **协议基线**：服务端与客户端均以 MCP 规范 **2026-07-28** 为唯一目标时代。不设计、不测试、不文档化 2025-03-26 时代的握手流程、`Mcp-Session-Id` 协商、`stateless_http` 兼容标志等历史行为。
- **测试重写**：按新协议断言（`_meta` 自描述、`Mcp-Method`/`Mcp-Name` header、`server/discover` 调用、`InputRequiredResult` 多轮往返）重写 `tests/test_services/test_mcp/` 下的服务端/客户端测试；只覆盖现代时代路径。
- **配置面**：`.env.example` 的 `MCP_TRANSPORT`、`MCP_AUTH_TYPE` 等设置保持应用层抽象，不引入时代开关。
- **文档更新**：`docs/how-to/enable-mcp-server.md`、`docs/tutorials/03-mcp-integration.md` 的客户端连接示例切换到新 API；`docs/features/feature-catalog.md` 5.4b 行措辞调整（移除 "2025-03-26 spec" 字样）。

无功能删除或破坏性 API 变更（应用层 Hecate HTTP API 不变；MCP 工具列表与行为不变）。

## Capabilities

### New Capabilities
（无新 spec——本 change 仅升级现有能力实现层，不引入新能力语义。）

### Modified Capabilities
- `mcp-server`：协议基线由 2025-03-26 升至 2026-07-28；server 端点要求在响应中携带规范的 `_meta` 标识、`server/discover` 必须可达；MCP 工具清单不变（仍为既有 `agent_chat` / `session_create` / `knowledge_search` 等 20+ 工具）；实现层由 fastmcp 2 迁移至 fastmcp 4（语义透明）。
- `mcp-client-real`：`HecateMCPClient` 内部实现由 v1 SDK 的 `ClientSession` + `streamablehttp_client`/`stdio_client` 三层机器迁移至 v2 的 `Client(target)` 自动协商 API；外部公开接口签名保持向后兼容；连接外部服务器时的时代协商由新 SDK 内部完成，Hecate 侧不暴露时代选项。

## Impact

- **代码**：`src/hecate/services/mcp/server.py`、`src/hecate/services/mcp/client.py`、`src/hecate/main.py`（两处挂载）、`.env.example`。
- **测试**：`tests/test_services/test_mcp/` 全部重写；`tests/conftest.py` 中 MCP 相关 fixture 按需更新。
- **依赖**：`pyproject.toml` 基础依赖三行变更；新增 `[dev]` 或测试 fixture 不变。
- **配置**：`MCP_SERVER_ENABLED`、`MCP_SERVER_HOST`、`MCP_SERVER_PORT`、`MCP_AUTH_TYPE`、`MCP_CLIENT_TIMEOUT` 等应用层设置保持不变。
- **文档**：`docs/how-to/enable-mcp-server.md`、`docs/tutorials/03-mcp-integration.md`、`docs/features/feature-catalog.md`（5.4b 行）。
- **ADR / 设计文档**：fastmcp 2→4 的类/挂载 API 变更属实现细节，记录在 design.md，不新增 ADR。
- **行为差异**：MCP 工具的外部可见接口（工具名、参数、返回值）零变化；Hecate 自有 HTTP API（`/v1/chat/completions` 等）零变化。客户端连接 Hecate 的 `/mcp` 时会看到新规范的协议头与 `_meta` 自描述，属于合规升级而非不兼容变更。

## 参考依据

- MCP 规范 2026-07-28（SEP-2575 无状态核心、SEP-2567 移除 Session-Id、SEP-2243 header 标准化、SEP-2549 ttlMs 缓存、SEP-2577 弃用 roots/sampling/logging）
- 官方 Python SDK v2.0.0 release notes
- fastmcp PR #4655（迁移至稳定 SDK 2.0.0）
- repo 内归档 `openspec/changes/archive/2026-06-07-mcp-server-mode`（D1/D3/D4 设计沿用）
- repo 内归档 `openspec/changes/archive/2026-07-14-mcp-connection-management`（5.4c 池/熔断/注册层公开接口稳定）
- repo 内归档 `openspec/changes/archive/2026-08-04-horizontal-scaling-validation`（多副本 MRTR 状态密封）
