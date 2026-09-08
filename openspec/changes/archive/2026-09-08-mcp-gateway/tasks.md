## 1. 数据模型与迁移

- [x] 1.1 新增 `src/hecate/models/gateway_target.py`：`GatewayTargetModel`（name、kind、base_url、spec、credentials、workspace_id、is_active、created_by，workspace 内 name 唯一约束）+ Pydantic Create/Read/Update schemas（Read 路径凭证脱敏）
- [x] 1.2 `ToolModel` 增加可空列 `target_id`（FK → gateway_targets），更新对应 schemas
- [x] 1.3 Alembic 迁移：创建 `gateway_targets` 表 + `tools.target_id` 列（纯增量，downgrade 可回退）

## 2. Target 注册与出网基线

- [x] 2.1 新增 `src/hecate/tools/gateway/targets.py`：target CRUD 服务（rest/mcp 两种 kind），含 name 唯一性、workspace 归属
- [x] 2.2 出网基线校验：非 loopback 主机强制 HTTPS，违规注册拒绝（结构化错误）；loopback HTTP 放行
- [x] 2.3 凭证脱敏：管理 API 读取路径统一 `_redact()`（值 → `***`），单测覆盖"任何响应不出现明文"
- [x] 2.4 单测：注册校验矩阵（合法 https / loopback http / 非 loopback http / 重复 name / 非法 kind）

## 3. OpenAPI 转换器

- [x] 3.1 新增 `src/hecate/tools/gateway/converter.py`：OpenAPI 3.x 最小解析（paths × GET/POST/PUT/PATCH/DELETE、path/query 参数、application/json requestBody、operationId 或 path 派生名）
- [x] 3.2 投影落库：每个 operation → ToolModel 行（`source="rest"`、name=`<target>__<operation>`、parameters=JSON Schema、`target_id` 指向 target）；unsupported 构造（callbacks/links/servers）跳过并生成 warning 清单
- [x] 3.3 导入报告：成功清单 + 跳过清单 + warning，非法文档整体拒绝（零行落库）
- [x] 3.4 单测：样例 spec 全链路（3 operation 导入 → 行数/命名/schema 断言）、malformed spec 拒绝、奇异文档 skip-with-warning

## 4. REST 执行器

- [x] 4.1 新增 `src/hecate/tools/gateway/executor.py`：`httpx.AsyncClient` 执行——arguments 按 operation schema 翻译为 method/path/query/body，base_url 钉死取 target 注册值（忽略 spec 内 servers）
- [x] 4.2 凭证注入：outbound 请求附加 target credentials；错误与日志路径不携带凭证
- [x] 4.3 结构化错误：非 2xx / 连接失败返回 target + operation + failure class（upstream error / connection failure），不自动重试；超时沿用 `MCP_REQUEST_TIMEOUT`
- [x] 4.4 单测：GET path 参数翻译、POST body 翻译、凭证注入、500/超时/连接拒绝三态错误

## 5. MCP 联邦（mcp kind target）

- [x] 5.1 target 注册时复用现有连接管理注册（`MCPServerRegistry`，lazy 连接），unregister 时反注册
- [x] 5.2 展示名投影：`tools/list` 时对 source=mcp 工具计算 `<target>__<mcp_tool_name>`（内部 `ToolModel.name` 不迁移）；跨 target 同名工具投影后无碰撞
- [x] 5.3 `tools/call` 反解：前缀名 → 内部名 + target，经 `MCPClientManager.call_tool` 转发；未知前缀拒绝
- [x] 5.4 单测：联邦列表、跨 target 同名去重断言、前缀调用转发、未知前缀拒绝

## 6. MCP 边界认证（DB-backed key）

- [x] 6.1 `tools/mcp/auth.py` 扩展：`x-api-key` 先查 `ApiKeyService.verify_key()`（hash 命中 → workspace/system scope，校验 active + 未过期），miss 落 legacy `settings.api_keys_list`（platform scope）；jwt/none 行为不变
- [x] 6.2 认证结果携带 caller identity（workspace_id / scope）进入请求上下文
- [x] 6.3 单测：DB key 命中、expired/is_active 拒绝、legacy key fallback、无效 key 拒绝

## 7. MCP 边界授权（policy pipeline 接线）

- [x] 7.1 新增 `src/hecate/tools/gateway/authz.py`：由 caller identity 构造 `PolicyContext`；`tools/list` 对候选目录（first-party + 本 workspace 联邦）跑 pipeline，HIDE/DENY 剔除；`tools/call` 全管道评估，DENY 与 REQUIRE_APPROVAL 短路拒绝（fail-closed：pipeline 异常按拒绝）
- [x] 7.2 workspace 隔离：非本 workspace 的联邦工具在 list 与 call 两路径均不可见/不可达
- [x] 7.3 platform scope（legacy key）只见 first-party 目录
- [x] 7.4 单测：5 层 × 两拦截点矩阵、跨 workspace 隐藏与拒绝、platform scope 目录范围、fail-closed

## 8. Server 接线与管理 API

- [x] 8.1 `create_mcp_server()` 增加可选网关注入（`tools/gateway/` 组件）；`main.py` 仅在 `GATEWAY_ENABLED=true` 时注入；flag 默认 off
- [x] 8.2 `GATEWAY_ENABLED` 进 `core/config.py` + `.env.example`
- [x] 8.3 管理 REST API `tools/api/gateway.py`：`/api/gateway/targets` CRUD（list/create/read/update/deactivate），Read 脱敏；flag off 时返回 404-class
- [x] 8.4 `ToolRegistry` 增加 `source="rest"` 分支路由到 executor（target 不可用抛结构化错误）
- [x] 8.5 analytics 归因：网关转发调用进 otel trace bridge tool span，附加 `workspace_id` / `gateway.target` 属性

## 9. 回归与验证

- [x] 9.1 first-party 零变化回归：开关两态 × `tools/list`/`tools/call` 与既有行为基线对比测试
- [x] 9.2 spec delta 逐条验收：mcp-gateway（9 requirement）、mcp-server（2 modified）、tool-registry（1 modified）场景覆盖
- [x] 9.3 全量四检查通过：`ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/`、`python -m pytest tests/ -q`
