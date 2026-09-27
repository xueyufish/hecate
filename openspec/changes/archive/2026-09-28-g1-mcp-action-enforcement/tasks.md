# Tasks

## 1. 规范前置修复

- [x] 1.1 修复 `openspec/specs/mcp-server/spec.md` 的主规范结构：将文件开头误用的 `## ADDED Requirements` 改为 `## Requirements`，使全部既有 Requirement 都处于主规范 Requirements 区域。已在主 spec 磁盘落地（ChatGPT 在本 change 之外直接修复），验证：`openspec validate g1-mcp-action-enforcement` 不再报告 "Archive would refuse this delta"，`openspec show mcp-server --type spec` 能列出全部 Requirement。

## 2. 先建立失败复现

- [x] 2.1 新增 MCP viewer 对 `agent_create/update/delete`、`knowledge_create/ingest`、`tool_create` 的拒绝测试；另覆盖无 workspace 成员资格的身份，断言失败前后数据库状态一致。（`tests/test_services/test_mcp_server.py::TestMCPG1ActionAuthorization`；修复前的失败复现及修复后的回归用例均已记录）
- [x] 2.2 新增 REST agent 写入口测试：viewer 对 `create/update/delete/import` 及技能关联、晋升、移除端点均得到 403，且拒绝后没有 Agent/Workflow 写入；editor 可完成同组操作并只能引用可访问的 Knowledge。（`tests/test_api/test_g1_rest_action_enforcement.py` 覆盖七个拒绝入口和 editor 正例）
- [x] 2.3 新增 `tool_execute` 负例：跨 workspace 同名工具、调用者伪造 workspace/绝对路径、独立的 `approval_required=true` 与 `risk_level=HIGH`、未知风险、缺少执行绑定、文件隔离及符号链接逃逸。原始失败复现为跨租户文件隔离；审查后扩充的用例用于最终契约验证。
- [x] 2.4 新增 `MCP_AUTH_TYPE=none` 下调用 `tool_execute` 与变更类工具的负例，以及默认配置匿名请求在工具执行前被 401 拒绝的断言。（`tests/test_services/test_mcp_server.py::TestMCPG1NoAuthMode`；当前行为已满足失败关闭 — 内部异常不外泄这条已绿，"区分未认证与认证被禁用"作为后续 contract pin）
- [x] 2.5 先运行上述新增用例，确认它们因当前旁路而失败；记录失败对应的入口和副作用断言，再进入修复任务。（5 红 6 绿，全部按预期）

## 3. 共享角色检查与 REST agent 写入口

- [x] 3.1 在 `core/deps_workspace.py` 新增 `ensure_workspace_role(ctx, minimum)`：system scope 按现有系统权限规则通过；其他身份必须同时有显式 workspace 和足够角色，否则抛 `WorkspaceRoleRequiredError`；REST 依赖翻译为既有 403 结构。
- [x] 3.2 在 `tools/mcp/server.py` 增加调用共享检查的 MCP 辅助函数；错误响应应保留明确的授权失败含义，不暴露内部异常。（`tools/mcp/server.py`：新增 `_require_editor(ctx)`）
- [x] 3.3 `studio/api/agents.py` 中 Agent 创建、更新、删除、导入及技能关联/晋升/移除写端点接入 editor 门槛；Knowledge 引用也按调用者 workspace 验证。七个拒绝入口和 editor 正例由 `test_g1_rest_action_enforcement.py` 覆盖。
- [x] 3.4 移除 MCP 与 REST agent 创建路径中非 system 无 workspace 时回退写入 zero workspace 的行为。system scope 的 bootstrap/种子写入行为须单独显式表达，不能借 `workspace_id or zero_uuid` 隐式决定。（REST `create_agent`：行 140—147 重写为 `workspace_id if not ctx.is_system_scope else uuid.UUID(int=0)`；MCP 部分随 task 4.1 一起改）
- [x] 3.5 运行任务 2 的 REST/MCP 角色用例及 `tests/test_tenant_isolation.py`，确认失败复现转绿且无跨 workspace 状态变化。（50 passed in 52s，REST 端红转绿；MCP 端仍在 4.1）

## 4. MCP 变更类工具角色门槛

- [x] 4.1 MCP `agent_create/update/delete`、`knowledge_create/ingest`、`tool_create` 在任何持久化或知识库副作用之前要求 editor 及以上角色，并要求非 system 身份具有显式 workspace。（`tools/mcp/server.py`：6 个工具替换内联检查为 `_require_editor(ctx)`；移除 `ctx.workspace_id or _BUNDLED_WS` 写回退）
- [x] 4.2 MCP Agent 更新/删除与 Knowledge 导入对租户按 workspace 过滤，对 system scope 保留按明确资源 ID 操作；Knowledge 列表仅显示本 workspace 可搜索的资源，内置工具目录保留共享只读可见性。受限身份在 MCP 工具入口统一拒绝。
- [x] 4.3 运行任务 2.1 的 viewer、受限身份和 editor 用例；逐项确认拒绝发生在副作用之前。（8/9 G1 测试转绿；唯一红的是 `test_tool_execute_write_file_workspace_isolation` —— 预期在 task 5 修复）

## 5. MCP `tool_execute` 的安全解析与执行

- [x] 5.1 由服务端 `AuthContext` 派生 workspace 和文件根 `Path(settings.WORKSPACE_ROOT) / str(ctx.workspace_id)`；缺少显式 workspace 的非 system 身份在解析工具前拒绝。执行器的文件路径校验必须基于解析后的根，并拒绝路径穿越及解析后指向根外的符号链接。（`tools/mcp/server.py::tool_execute`：派生 `effective_root`；`workspace_id is None` 的非 system 身份在构建 executor 前显式拒绝）
- [x] 5.2 将工具解析、元数据授权和最终执行绑定到同一个确定的工具定义：数据库查询只含调用者可见范围；歧义和来源冲突在副作用前拒绝；允许的内置工具直接交给同一内置 executor，不再把名称交给 Registry 重新解析。外租户同名记录不会影响当前租户。
- [x] 5.3 `approval_required=true`、`risk_level=HIGH`、未知风险或缺少授权绑定的工具在副作用前显式拒绝。此 MCP 直接入口只放行 `web_search`、`read_file`、`write_file`、`list_files`；代码执行、Memory、浏览器及远程工具在本入口拒绝，未来放行需独立的授权绑定。
- [x] 5.4 文件工具仅使用服务端 workspace 根；调用者提供的 workspace、身份、根目录或绝对路径不得扩大权限。成功与拒绝记录主体、workspace、工具身份和判定结果，不记录参数或凭据；MCP 返回可观察的结果或错误。
- [ ] 5.5 运行任务 2.3 的工具歧义、高风险/待审批拒绝、路径越界、符号链接逃逸和跨租户文件隔离用例；每个拒绝用例断言没有文件、数据库或远程调用副作用。此前受影响集合为 214 passed、1 skipped；最后新增的 MCP 正例已通过，但最终 MCP 重跑中 11 个 `tmp_path` 用例被 Windows 沙箱对 pytest 临时目录的访问限制阻断。符号链接负例还须在可创建符号链接的 CI 环境执行。

## 6. `MCP_AUTH_TYPE=none` 失败关闭

- [x] 6.1 `MCP_AUTH_TYPE=none` 时在 request state 中明确标记认证已禁用；受保护工具不得获得 `AuthContext` 或进入业务执行，错误响应区分未认证与认证被禁用。（`MCPAuthMiddleware` 在 none 模式注入 `state["auth_disabled"] = True`；`get_transport_auth_context` 区分抛 `MCP authentication disabled (...)` 与 `unauthenticated: ...`）
- [x] 6.2 运行任务 2.4 用例，确认 none 模式下 `tool_execute` 与变更类工具均失败且无副作用；默认认证配置匿名请求仍在调用工具前被 401 拒绝。（`TestMCPG1NoAuthMode` 2 项绿；既有 `test_anonymous_mcp_request_rejected` 默认配置 401 仍绿——25 passed in mcp_server suite）

## 7. 收尾验证与剩余门槛登记

- [ ] 7.1 运行 `ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/`，以及受影响的 MCP、agent API、tenant isolation 和 tool 测试；以本次最终代码的实际结果为准更新证据。Ruff、mypy、OpenSpec 校验通过；此前受影响集合 214 passed、1 skipped，最终 MCP 重跑 31 passed、11 个因 pytest 临时目录访问限制报错，待可运行环境复核。
- [x] 7.2 对照演进方案 §八的 MCP viewer 场景记录逐条验收证据；基线文档只标注本 change 实际关闭的入口和动作，不将 G1 总体标为已关闭。（`docs/research/platform-evolution-baseline.md` §7 G1 行已对齐 MCP 直接入口、REST/MCP 写入口及剩余边界）
- [x] 7.3 登记未覆盖的 Workflow/Chat 工具入口(B2)、定时执行路径(B3)、其他 REST 配置写入口、评估调用、生产环境禁止 `MCP_AUTH_TYPE=none` 的启动门禁、step7 Action 服务统一接入，作为后续关闭 G1 的明确门槛。（基线文档 §7 G1 行内联登记）
- [ ] 7.4 推送前确认当前分支为 `fix/g1-mcp-action-enforcement`；推送仍需用户在聊天中显式批准。
