# Design — 5.4a MCP Gateway

## Context

底座全部就绪（见 proposal 与 [决策记录](../../../docs/research/2026-09-mcp-gateway-explore-decisions.md)）：`/mcp` FastMCP server（2026-07-28 spec，`main.py:506-518` 挂载，`tools/mcp/server.py` ~15 个 first-party 硬编码工具）；MCP Client 栈（pool/breaker/health，`tools/mcp/`）；外部 MCP 工具已投影 ToolModel 行（`tools/mcp/sync.py`）；5 层 policy pipeline（`tools/policy/`，**无生产消费者**）；workspace 级凭证 `ApiKeyModel` + `ApiKeyService.verify_key()`。约束：first-party 工具行为零变化（D1 渐进式）；`httpx`/`pyyaml` 已在依赖中，零新增第三方依赖。

## Goals / Non-Goals

**Goals:**
- 网关组件（target 注册、OpenAPI 转换器、REST 执行器、边界 authz）以可注入模块并入现有 `/mcp` server，feature flag 控制
- MCP 协议边界成为 policy pipeline 的第一个生产消费者（visibility 过滤 + execution 管道）
- 设计开放点①–④（决策记录文末）在本文全部裁决

**Non-Goals:**
- proposal 的 not-in-scope 清单原样有效（OAuth、SSE 源、11.16/11.17、custom 执行、first-party 注册化、resources/prompts 联邦等）
- 不引入新第三方依赖

## Decisions

### D-1 · Target 存储形态：新表 `gateway_targets`（裁决开放点①）

新增 `GatewayTargetModel`（`src/hecate/models/gateway_target.py`）：`name`（workspace 内唯一）、`kind`（`rest` / `mcp`）、`base_url`、`spec`（JSON，OpenAPI 文档原样存储）、`credentials`（JSON，静态 header/key）、`workspace_id` FK、`is_active`、`created_by`。`ToolModel` 增加可空列 `target_id` FK。

**为何不复用 `PluginModel`**：plugin 的 `entry` 是 `mcp://` 生命周期语义（enable/disable 驱动连接注册，5.5c 已占用），credentials 与 spec 无处安放，且 agent-plugins-ingestion 已把 plugin 类型语义占满。独立表让 authz 过滤（按 workspace 查 target）和将来 10.8 vault 化（`credentials` 列整体替换为 vault 引用）都是局部改动。
**备选已否决**：全部塞 `ToolModel`（spec 几十 KB 进工具行，读写放大）；复用 `PluginModel`（语义错位，见上）。

### D-2 · 联邦工具的命名：存储时前缀（rest）+ 展示时前缀（mcp）（裁决开放点③）

- `rest` 工具：导入时即以 `<target>__<operation>` 落 `ToolModel.name`（行是导入产物，无存量）。
- `mcp` 工具：`MCPToolSync` 已同步的行**不做数据迁移**（内部名保持原样，engine 路径零破坏）；网关在 `tools/list` 投影时计算展示名 `<target>__<mcp_tool_name>`，`tools/call` 收到前缀名后反解回内部名。
**备选已否决**：一次性迁移存量 mcp 行改名（破坏 agent 绑定与 policy 引用，收益为零）。

### D-3 · authz 接线：verify_key → PolicyContext → pipeline 两拦截点（裁决开放点②）

请求进入 `/mcp` 时：`ApiKeyService.verify_key()` 解析出 `ApiKeyModel` → 构造 `PolicyContext(workspace_id, scope, key_id)`（pipeline 现有 context 结构，不扩字段）。两个拦截点：
- `tools/list`：对候选工具目录（first-party + 本 workspace 联邦）跑 pipeline，HIDE/DENY 剔除后返回；
- `tools/call`：单工具全管道评估，DENY/REQUIRE_APPROVAL 短路（v1 网关路径 REQUIRE_APPROVAL 按 DENY 处理——外部 MCP 客户端没有审批 UI 可等待）。
参与的层：PluginAvailability、Profile、Visibility、Security（Mode 层是 agent 配置语义，网关路径不适用）。
**备选已否决**：只做 workspace 归属检查不过 pipeline（绕过了 2026-06-19 granular-tool-security 的既有语义，两套标准）。

### D-4 · 身份映射：legacy env key ≈ 平台域

`MCP_AUTH_TYPE=api_key` 下先查 DB key（hash 命中 → workspace/system scope），miss 再落 `settings.api_keys_list`（legacy，platform scope，只见 first-party）。`jwt` / `none` 模式行为不变（jwt v1 不解析 workspace，按 platform scope）。迁移零成本，老客户端不坏。

### D-5 · REST 执行器：`httpx.AsyncClient` + base_url 钉死

复用 `httpx`（已在依赖）。执行时 `base_url` 一律取 target 注册值，**忽略** OpenAPI 文档内的 `servers` 覆盖（防 SSRF：文档内容是租户可控输入，注册时已过出网基线 D8）。超时沿用 `MCP_REQUEST_TIMEOUT`；不自动重试（spec 行为）。

### D-6 · OpenAPI 解析：自研最小转换器，不引库

支持的子集：OpenAPI 3.x 的 `paths` × GET/POST/PUT/PATCH/DELETE、path/query 参数、`requestBody`（`application/json`）、`operationId`（缺失时从 path 派生 kebab-case 名）。`callbacks`/`links`/`servers` 跳过并出 warning。导入期一次性校验 + 投影，运行期零解析（spec 存 `GatewayTargetModel.spec`，执行只用投影结果 + `target_id`）。
**备选已否决**：引入 `openapi-core` 等（重依赖、校验语义过强，导入期报错会变成运行期风险面）。

### D-7 · 网关模块布局：`src/hecate/tools/gateway/`

四个模块：`models.py`（GatewayTargetModel 若 models/ 惯例要求则落 `src/hecate/models/gateway_target.py`）、`targets.py`（target 注册/CRUD + 出网基线校验）、`converter.py`（OpenAPI → ToolModel 投影）、`executor.py`（REST 执行器）、`authz.py`（PolicyContext 构造 + 目录过滤）。`server.py` 的 `create_mcp_server()` 增加可选注入；`main.py` 挂载逻辑只在 `GATEWAY_ENABLED=true` 时注入。管理 REST API 落 `tools/api/gateway.py`（`/api/gateway/targets` CRUD）。

### D-8 · 凭证脱敏与日志

`credentials` 读取路径统一过 `_redact()`（值替换为 `***`，仅管理 API 层）； outbound 注入在 executor 内部完成，错误与日志路径不携带凭证（结构化错误只含 target/operation/failure class）。analytics 沿用 otel trace bridge 的 tool span，附加 `workspace_id` / `gateway.target` 属性。

## Risks / Trade-offs

- [/mcp 是既有对外面，回归代价高] → `GATEWAY_ENABLED` 默认 off；first-party 行为零变化的回归测试（开关两态 × tools/list 对比基线）
- [policy pipeline 首次生产接线，语义未经实战] → authz 模块单测覆盖 5 层 × 两拦截点矩阵；DENY fail-closed（pipeline 异常按拒绝处理）
- [REQUIRE_APPROVAL 在网关路径按 DENY，比内部路径更严] → 文档明示；审批式网关调用留待 approval 状态机暴露异步 API 后再开
- [OpenAPI 子集转换器遇到奇异文档] → 导入期显式 warning + 跳过清单（不静默）；spec 原样存储，未来换转换器可重放
- [联邦工具目录膨胀] → v1 无分页，`tools/list` 上限 500 条按名排序（裁决开放点④：分页推迟到有真实规模信号再做）

## Migration Plan

1. Alembic 迁移：新增 `gateway_targets` 表 + `tools.target_id` 可空列（纯增量，可独立合入）
2. `GATEWAY_ENABLED=false` 全量发布——行为与现状逐字节一致
3. 目标环境开启 flag → 注册首个 target 验证 → 对外
4. 回滚：flag 置 off 即回到步骤 2 状态；表与列保留无害

## Open Questions

无——决策记录文末 4 个开放点已全部裁决（①→D-1、②→D-3、③→D-2、④→Risks 末条）。
