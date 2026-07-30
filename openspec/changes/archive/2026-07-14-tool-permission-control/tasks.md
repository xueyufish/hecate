## 1. Pipeline Core — 管道核心

- [x] 1.1 创建 `src/hecate/engine/policy_pipeline.py` — `PolicyDecision` 枚举（ALLOW/DENY/HIDE/REQUIRE_APPROVAL/EXECUTE_SANDBOX/PASSTHROUGH），`PolicyContext` dataclass（tool_name、tool_meta、arguments、agent_id、workspace_id、channel_snapshot、execution_context），`PolicyLayer` ABC（`evaluate(tool_info, context) -> PolicyDecision`），`ToolPolicyPipeline` 类（`evaluate_visibility(tools, context) -> list` 和 `evaluate_execution(tool, context) -> PolicyDecision`，带 DENY 短路）
- [x] 1.2 创建 `src/hecate/engine/policy_layers.py` — 5 个具体层实现

## 2. Layer Implementations — 层实现

- [x] 2.1 `PluginAvailabilityLayer` — 通过内存字典查找（插件名 → 已启用 bool）检查插件启用状态。对于内置工具（source="builtin"），始终 ALLOW。对于 MCP 工具（source="mcp"），检查 MCPServerRegistry.has_server()。对于自定义工具，始终 ALLOW。
- [x] 2.2 `ProfileLayer` — 评估来自数据库的 `ToolPolicyRuleModel` 规则。加载工作空间级 + 代理级规则，按 action（DENY→ASK→ALLOW）然后 priority 排序，通过 fnmatch 匹配工具名称，通过 fnmatch 检查 arg_conditions。如果没有规则匹配，返回 ALLOW/DENY/REQUIRE_APPROVAL 或 PASSTHROUGH。
- [x] 2.3 `VisibilityLayer` — 包装现有的 `ToolGateEvaluator`。评估 `available_when` 表达式。返回 HIDE（在可见性过滤期间）或 ALLOW。保持失败关闭语义。
- [x] 2.4 `SecurityLayer` — 包装现有的 `ToolAccessPolicy`。调用 `tool_access_policy.evaluate(tool_meta, rules, context, arguments)` 并将 `AccessDecision` 映射到 `PolicyDecision`。内部逻辑零变更。
- [x] 2.5 `ModeLayer` — 评估 `PermissionMode`。DEFAULT：不变地返回 SecurityLayer 决策。RESTRICTED：如果工具不在允许列表中则返回 DENY。AUDIT：将 DENY 覆盖为 ALLOW 并记录 WARNING，保留 REQUIRE_APPROVAL。

## 3. Data Models — 数据模型

- [x] 3.1 创建 `src/hecate/models/tool_policy.py` — `ToolPolicyRuleModel`（id、workspace_id、agent_id 可空、tool_pattern str、action str [allow/deny/ask]、priority int、arg_conditions JSON），`AgentPolicyConfigModel`（id、workspace_id、agent_id 唯一、mode str、tool_allowlist JSON、tool_denylist JSON），Pydantic schemas（Create/Update/Read）
- [x] 3.2 创建 Alembic 迁移 `alembic/versions/v0c1d2e3f4a5_add_tool_policy_models.py`

## 4. Worker Integration — Worker 集成

- [x] 4.1 更新 `src/hecate/engine/workers/llm_worker.py` — 将对 `ToolGateEvaluator` 的 `_filter_tools()` 调用替换为 `pipeline.evaluate_visibility(tools, context)`。管道由各层构建，VisibilityLayer 处理 HIDE 决策。
- [x] 4.2 更新 `src/hecate/engine/workers/tool_worker.py` — 将对 `ToolAccessPolicy` 的 `_check_access()` 调用替换为 `pipeline.evaluate_execution(tool, context)`。将 PolicyDecision 映射到现有执行流程（EXECUTE/EXECUTE_SANDBOX/REQUIRE_APPROVAL/DENY）。
- [x] 4.3 更新 `src/hecate/services/orchestration/engine_port_adapter.py` — 构建包含所有 5 层的 `ToolPolicyPipeline`，注入到 LLMWorker 和 ToolWorker。

## 5. REST API — REST API

- [x] 5.1 创建 `src/hecate/api/management/tool_policies.py` — 路由前缀为 `/api/tool-policies`：`GET /rules`（列表，按 agent_id 过滤），`POST /rules`（创建），`PUT /rules/{id}`（更新），`DELETE /rules/{id}`（删除），`GET /agents/{agent_id}/config`（获取代理配置），`PUT /agents/{agent_id}/config`（更新代理配置）
- [x] 5.2 在 `src/hecate/main.py` 中注册 `tool_policies_router`

## 6. Audit Logging — 审计日志

- [x] 6.1 向 `ToolPolicyPipeline.evaluate_execution()` 添加审计日志 — 以 DEBUG 级别记录每层的决策，包含 tool_name、agent_id、层名称、决策、原因。在 AUDIT 模式下，当 DENY 被覆盖为 ALLOW 时记录 WARNING。

## 7. Backend Tests — 后端测试

- [x] 7.1 测试 `ToolPolicyPipeline` — DENY 短路、HIDE 短路（仅可见性）、全部通过返回 ALLOW
- [x] 7.2 测试 `PluginAvailabilityLayer` — 插件启用/禁用、MCP 服务器已注册/未注册、内置工具始终允许
- [x] 7.3 测试 `ProfileLayer` — 工作空间级规则、代理级规则优先级、glob 匹配、arg_conditions 匹配、无规则 passthrough
- [x] 7.4 测试 `VisibilityLayer` — 表达式通过/失败、失败关闭、无表达式 passthrough
- [x] 7.5 测试 `SecurityLayer` — 危险模式拒绝、高风险 require_approval、沙箱路由（验证现有 ToolAccessPolicy 行为被保留）
- [x] 7.6 测试 `ModeLayer` — DEFAULT passthrough、RESTRICTED 拒绝非白名单工具、AUDIT 覆盖 deny→allow 并带警告、AUDIT 保留 require_approval
- [x] 7.7 测试 REST API — CRUD 规则、代理配置、404 对于不存在的资源
- [x] 7.8 测试向后兼容性 — 没有策略配置的代理使用 DEFAULT 模式，现有行为不变

## 8. Verification — 验证

- [x] 8.1 运行 `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 错误
- [x] 8.2 运行 `mypy src/` — 0 错误
- [x] 8.3 运行 `python -m pytest tests/test_engine/test_policy_pipeline.py tests/test_api/test_tool_policies.py -q` — 全部通过
- [x] 8.4 运行 `python -m pytest tests/test_engine/test_tool_access.py tests/test_engine/test_tool_gate.py -q` — 现有测试仍然通过（向后兼容）
