## Context

5.4b 的现状与约束（详见 `proposal.md` Why 段）：

- 现有 MCP server：`src/hecate/services/mcp/server.py` 通过 `fastmcp>=2.0.0` 的 `FastMCP` 类构建，`main.py:733`/`784` 两处 `_mcp.http_app(path="/mcp")` 挂载到 FastAPI 应用；依赖 `pyproject.toml` 中 `fastmcp>=2.0.0` 与 `mcp>=1.0.0`。
- 现有 MCP client：`src/hecate/services/mcp/client.py` 使用 v1 SDK 的 `ClientSession` + `streamablehttp_client` + `stdio_client` + `AsyncExitStack` 三层机器；被 5.4c 的 `connection.py`（连接池、熔断、注册）包装。
- 决策：直接以 MCP 规范 **2026-07-28** 为唯一目标时代，不为 2025 时代客户端做兼容层（Hecate 当前为 alpha，未商用）。
- 升级路径官方与社区均已就位：官方 Python SDK `mcp 2.0.0` stable（2026-07-28 当天发布），fastmcp 4.0 同日迁移到稳定 SDK 2.0（PR #4655）；decorator 风格工具定义 API 在两个版本中均保持兼容。

## Goals / Non-Goals

**Goals:**
- 以最小化应用代码改动完成 server 端 fastmcp 2→4 与 client 端 mcp 1→2 的 API 适配；20+ 工具定义、HecateMCPClient 公开接口、D4 应用层 session 模型全部保持稳定。
- 使 Hecate 同时满足：可作为多副本无状态部署、可被普通 round-robin 负载均衡路由、自身可消费混合时代外部 MCP 服务器。
- 顺手收下协议升级带来的合规与运维收益：RFC 9207 / OAuth 强化、tls body limit、cache 提示。
- 在升级窗口内闭环 P3 收尾（roadmap 第 651 行 `[ ]` 工作项），不留半成品。

**Non-Goals:**
- 不实现 MCP Tasks 扩展（SEP-2663）。官方 SDK v2.0.0 暂未包含；fastmcp 4 有但其能力等价于把长任务切成"返回 task handle + 客户端轮询"，Hecate 的 `agent_chat` 走 `WorkflowExecutionService.execute()` 同步路径仍合适。Tasks 留作后续按需评估。
- 不为 2025 时代客户端保留 fallback：服务端不主动握手 `initialize`，不识别 `Mcp-Session-Id`，不引入 `stateless_http=True` 兼容标志；客户端连老服务器时由 SDK 内部 `Client(mode='auto')` 自动回退，Hecate 侧不暴露时代选项。
- 不引入 MRTR↔HITL 闭环（resolver dependency injection）。值得做，但属增量能力，scope 内不强制；若 tasks 中顺手实现可在 proposal 中加 ADDED 要求。
- 不重构 `services/mcp/connection.py`（5.4c 连接池/熔断/注册）的公开接口。
- 不重写 Hecate 的 MCP 工具集合；不引入新工具；不调整 D4 session 模型。

## Decisions

### D1: SDK 升级策略 —— 跨大版本，单步替换

`pyproject.toml`：
```toml
"fastmcp>=4.0.0b3,<5",   # was: >=2.0.0；fastmcp 4.0 stable 尚未发布（截至 2026-08-22 PyPI 最新为 4.0.0b3）
"mcp>=2.0.0",            # was: >=1.0.0；mcp 2.0.0 stable 已发布
"mcp-types>=2.0.0",      # 新增（与 mcp 锁步发布的 standalone wire-types 包）
```

**Rationale**：fastmcp 4.0.0b3 是 4.x 线的最新 beta，实现了 2026-07-28 规范（header 标准化、`_meta` 自描述、`server/discover`、双时代伺服），是 5.4b 满足"按 2026-07-28 规范落地"决策的唯一可用版本。mcp 2.0.0 stable 提供客户端能力。beta 风险通过 tasks 8.x 的端到端验证与 7.x 的 CI gates 兜底；fastmcp 4 stable 发布后下次升级（>=4.0.0 stable 替换）即可消化。

**Implementation 期方向决策（2026-08-22，apply 阶段）**：
- 当前 pyproject 是 `fastmcp>=2.0.0` + `mcp>=1.0.0`，pip 解析到 fastmcp 3.4.7（满足 >=2.0.0 下限的最新）+ mcp 1.29.0
- 跨版实际是 fastmcp **3.4.7 → 4.0.0b3**（一个大版本跨）+ mcp **1.29.0 → 2.0.0**（一个大版本跨）
- 不存在 fastmcp 2 中间步骤

**Alternatives considered**：
- 留在 fastmcp 3.4.7（不满足 2026-07-28 规范）→ 拒绝：5.4b catalog 决策落空
- 切换到官方 SDK `mcp.MCPServer` 放弃 fastmcp → 拒绝：fastmcp 4.0.0b3 已基本就位，切换会引入新的设计决策与代码改动
- 跨大版本分步 → 不必要：fastmcp 与 mcp 各只跨一版

### D2: 服务端栈选型 —— 继续 fastmcp，不切换到官方 `MCPServer`

继续使用 fastmcp 4（PrefectHQ jlowin 维护），而非官方 SDK 的 `MCPServer` 类。

**Rationale**：
- fastmcp 4 本身构建在 `mcp` 2.x 之上（PR #4655 显式声明 `mcp>=2.0.0,<3.0.0`），不是真的"双依赖"。
- fastmcp 4 已提供 MCP Apps 扩展、background tasks、企业 auth 等 4.0 头部能力，对未来增量友好。
- `mcp.MCPServer` 与 `FastMCP` 类 API 形状一致（`@mcp.tool` 装饰器保留），但 fastmcp 在 Hecate 代码里已使用，统一替换面更小。

**Alternatives considered**：
- 切换到 `mcp.MCPServer`、去掉 fastmcp 依赖 → 拒绝：Hecate `services/mcp/server.py` 已深度使用 fastmcp 风格的 `http_app()` 挂载；fastmcp 4 的 4.0 增量（MCP Apps / tasks）官方 SDK 暂未追平。

### D3: Server 挂载方式 —— 跟随 fastmcp 4 upgrade guide

fastmcp 2→4 的破坏性变更集中在：(a) `http_app(path=...)` 命名参数变化或弃用；(b) `FastMCP(...)` 构造函数的服务相关参数（如 host/port/stateless_http/transport_security）迁移至 `mcp.run(...)` 或 `mount_path`；(c) `lifespan` 与 ASGI 子应用合并行为调整。

具体迁移路径：
1. `create_mcp_server()` 返回的 `FastMCP` 实例构造改为 fastmcp 4 推荐形态（仅声明 server 本体：name、instructions、auth provider）。
2. `main.py` 的 ASGI 挂载按 fastmcp 4 upgrade guide 推荐方式实现：
   ```python
   from fastmcp.utilities.lifespan import combine_lifespans
   mcp_app = mcp.http_app(path="/")  # 关键：用 path="/"，避免 /mcp/mcp double prefix
   app = FastAPI(
       title="Hecate",
       lifespan=combine_lifespans(app_lifespan, mcp_app.lifespan),
   )
   app.mount("/mcp", mcp_app)  # MCP endpoint at /mcp
   ```
   - `path="/"` 是 fastmcp 4 + FastAPI 挂载推荐形态（gofastmcp.com/integrations/fastapi）；旧写法 `path="/mcp"` 会因 default + mount 双重前缀产生 `/mcp/mcp`（PR #2962）
   - `combine_lifespans` 仍为 fastmcp 4 推荐 lifespan 合并方式（FastAPI/Starlette 不会自动执行挂载子应用的 lifespan，必须显式传递）
3. `MCP_SERVER_HOST`/`MCP_SERVER_PORT`/`MCP_AUTH_TYPE` 等应用层 setting 保持现状，由 Hecate 在挂载外层 wrap。

### D4: Client 重写边界 —— 单文件收敛于 `client.py`

`HecateMCPClient` 公开接口保持完全兼容：
- `connect(server_url, transport="http", ...)` / `connect_stdio(command, args, ...)`
- `list_tools()`
- `call_tool(tool_name, arguments, **opts)`
- `disconnect()`
- `egress_filters=` 构造参数保持

实现层：把 `AsyncExitStack` + `streamablehttp_client`/`stdio_client` + `ClientSession` 三层替换为 v2 `Client(target, mode='auto')`。`Client` 本身是 async context manager（`async with client:` 进入即连接），通过 `await client.list_tools()` / `await client.call_tool(...)` 调用，内部自动协商协议时代与 MRTR 输入回环。

`EgressFilter` 链在 `call_tool` 返回后立即应用，调用方零变化。

`MCPClientManager`（`connection.py`）`_create_client` 仍按构造 `HecateMCPClient`，逐字不变；连接池、熔断器、注册、TTL 缓存等 5.4c 能力不动。

### D5: 协议时代基线 —— 单时代目标

服务端与客户端代码路径只走 2026-07-28：
- 服务端：fastmcp 4 默认伺服 2026-07-28（同时也对 2025 时代客户端响应，但 Hecate 不测试、不文档化该路径）。
- 客户端：`Client(target, mode='auto')`，内部按协议自动协商。Hecate 侧不显式读取 `client.protocol_version` 做业务分支（除用于 `GET /api/mcp/connections` 诊断展示）。

不引入：
- `mcp_session_id` 配置项；
- `legacy_handshake` / `mode` 配置开关；
- `stateless_http` 兼容标志；
- `MCP_PROTOCOL_VERSION` 显式 setting（Hecate 不应允许 downgrade）。

### D6: MRTR↔HITL 增量 —— 留空，不强制

`mcp` 2.x 提供 `Resolve(fn)` 参数注入机制与 `InputRequiredResult` 多轮往返。理论上可把 Hecate 现有的 `ApprovalCallback`（APPROVAL_ASKED/DECIDED 持久化）通过 `Resolve` 注入工具定义，把"工具中途需要审批"从同步阻塞升级为跨请求往返。

**Rationale 留空**：scope 外的架构动作（涉及 `WorkflowExecutionService` 与 approval 状态机），当前 P3 收尾窗口不展开；任务清单中预留一个 spike 任务做可行性验证，结论如可落在 5.4b 内再补 ADDED 要求，否则作为独立 follow-up change。

### D7: 4 MiB body limit —— 加 pre-check

官方 SDK 在 Streamable HTTP 入口处内置 4 MiB 上限（HTTP 413）。Hecate 的 `knowledge_ingest` 工具理论上可能超过该阈值（文档/语料批量导入）。

**Decision**：接受 SDK 默认 4 MiB 上限。`knowledge_ingest` 若需更大负载，引入单独的 upload + job-id 模式（异步），不通过 MCP 工具调用体传输大文件。此事作为 tasks 阶段的"超阈值功能测试"任务暴露，结论如确需提升 limit 需在 apply 阶段提 ADDED 要求。

### D8: RequestStateSecurity —— 推迟

MRTR 的 `request_state` 默认 per-process 密钥密封，多副本部署需 `RequestStateSecurity(keys=[...])`。当前 Hecate MCP server 端工具不依赖 MRTR 状态（非 HITL 工具），暂不需要；如有需要，在 tasks 阶段加 `MCP_REQUEST_STATE_KEYS` 配置项由 ops 配置。

### D9: 测试策略 —— 重写不迁移

`tests/test_services/test_mcp/` 既有测试按 v1 SDK + 2025-03-26 协议断言编写，全部重写：
- `test_mcp_server.py`：按 fastmcp 4 + 2026-07-28 断言（`MCP-Protocol-Version` header、`_meta` 自描述、`server/discover`、`Mcp-Method`/`Mcp-Name` 校验、4 MiB limit）。
- `test_mcp_client.py`：按 mcp 2.x `Client` 断言（auto mode 协商、MRTR 重试、ttlMs 缓存）。
- `test_mcp_connection_manager.py`：5.4c 层不变，沿用既有测试（必要时小幅更新以匹配新 HecateMCPClient 内部构造）。

## Risks / Trade-offs

- **[R1] fastmcp 2→4 跨两个大版本，breaking changes 清单可能遗漏** → 缓解：tasks 阶段第一步专做"fastmcp 4 upgrade guide 通读 + breaking change 矩阵"，列出全部适配点；按矩阵逐条 tick。
- **[R2] 5.4c 连接池层在 HecateMCPClient 内部实现变更后，可能出现 API surface 兼容微差** → 缓解：保持 HecateMCPClient 公开签名一字不变（见 D4）；tasks 阶段跑 5.4c 既有测试，回归失败即修复，不向下扩散。
- **[R3] 官方 SDK 4 MiB 上限可能在某些 Hecate 工具（`knowledge_ingest`）上触发** → 缓解：tasks 中加入"大 payload 集成测试"，识别真实工具的 body 大小分布；如确认影响，按 D7 引入异步 upload。
- **[R4] mcp-types 包迁移到独立包可能引发 import 错误** → 缓解：Hecate 当前未直接 `from mcp.types import ...`（依赖间接解析），tasks 中加 grep 验证；如发现直接 import 改为 `from mcp_types import ...`。
- **[R5] 多副本 MRTR 状态密封 key（RequestStateSecurity）若未配置可能造成 multi-replica 部署的 MRTR 工具失败** → 缓解：当前 MCP 工具不依赖 MRTR（无 HITL 工具）；tasks 中加清单确认。
- **[R6] 与 P3 收尾另一项 6.27 Browser Automation 同期进行** → 缓解：6.27 不在 5.4b scope，独立 worktree，独立推进；本 change 不阻塞也不依赖。
- **[R7] 内部 MCP 客户端代码（如示例调用方、教程）使用 v1 API** → 缓解：`docs/tutorials/03-mcp-integration.md`、`docs/how-to/enable-mcp-server.md` 等纳入更新范围。

## Migration Plan

1. **依赖升级（原子）**：`pyproject.toml` 三行变更；`uv pip install -e ".[dev]"` 验证；CI ruff/mypy 仍绿。
2. **Server 端迁移**：
   a. fastmcp 2→4 upgrade guide 通读，列 breaking change 矩阵。
   b. `services/mcp/server.py` 重写 `create_mcp_server()` 工厂函数（工具装饰器不动）。
   c. `main.py` 调整挂载方式（`http_app` → fastmcp 4 推荐形态）。
3. **Client 端迁移**：`services/mcp/client.py` 单文件替换 `AsyncExitStack`+`ClientSession` 机器为 `mcp.Client`；公开接口零变化；`EgressFilter` 钩子点位置保持。
4. **测试重写**：按 D9 重写 MCP server / client 测试；保留 5.4c 连接管理测试不变。
5. **文档更新**：`docs/how-to/enable-mcp-server.md`、`docs/tutorials/03-mcp-integration.md`、`docs/features/feature-catalog.md`（5.4b 行更新到 2026-07-28 时代）。
6. **本地端到端验证**：`uvicorn hecate.main:app --reload` 启动；用 curl/httpx 或 MCP Inspector（2026-07-28 era）连 `/mcp`，跑通 `server/discover`、`tools/list`、`tools/call`；路由经 main app 而非绕过；验证 `MCP-Protocol-Version: 2026-07-28` 出现在响应头。
7. **CI 全套验证**：`ruff check src/ tests/` + `ruff format --check src/ tests/` + `mypy src/` + `python -m pytest tests/ -q`。
8. **归档**：执行 `/opsx-archive` 之前同步 `docs/design/positioning.md`（roadmap 第 651 行从 `[ ]` 转 `[x]`）。

**回滚策略**：依赖大版本升级如 CI 失败，git revert 单 commit 即可（fastmcp 2 + mcp 1.x 配置 + 对应代码均在同一 change 内）。

## Resolved Questions（探索阶段已确认）

- **OQ1（lifespan 合并）**：✅ fastmcp 4 仍以 `fastmcp.utilities.lifespan.combine_lifespans` 为推荐方式；FastAPI 集成文档（gofastmcp.com/integrations/fastapi）确认 `combine_lifespans(app_lifespan, mcp_app.lifespan)` 模式。Apply 阶段无需做选型，直接按此形态实现 tasks 3.2。
- **OQ2（`mcp.types` alias）**：✅ 官方 SDK v2.0.0 stable 与 fastmcp 4 升级指南双重确认：`from mcp.types import X` 仍可解析（指向 `mcp_types.X`），但字段名已转 snake_case（`inputSchema` → `input_schema`、`mimeType` → `mime_type`）。Tasks 2.4 的 import smoke test 与 tasks 1.2 的 `from mcp.types` 用法盘点已覆盖此点。
- **OQ3（OAuth / RFC 9207）**：问题本身被重新框定。RFC 9207 `iss` 校验作用域为 OAuth **客户端**在授权响应阶段验证 issuer；Hecate MCP server 端是资源服务器（api_key/jwt 直通），不发起 OAuth 流程，无 iss 校验面。客户端侧 mcp 2.x `Client` 在连接 OAuth-protected 外部服务器时自动启用 RFC 9207 校验（SEP-2468），Hecate 侧零代码获得。OAuth 作为 Hecate MCP_AUTH_TYPE 新增选项属独立 feature，scope 外保持原状。
- **Pre-existing bug 顺手修**：fastmcp PR #2962 指出 `mcp.http_app()` 默认 `path="/mcp"`，若 `app.mount("/mcp", mcp_app)` 挂载，最终端点为 `/mcp/mcp`（double prefix）。Hecate `main.py:733` 当前写法命中此 bug。同时，PR #2962 强调 FastAPI/Starlette 不会自动执行挂载子应用的 lifespan，必须显式 `FastAPI(lifespan=mcp_app.lifespan)` 或 `combine_lifespans(...)` 传递。两项均纳入 tasks 3.2 的迁移实现（作为升级窗口里的免费修复）。
