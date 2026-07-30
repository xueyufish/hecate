## 1. 引擎层 — 枚举和数据类（`engine/tool_access.py`）

- [x] 1.1 创建 `engine/tool_access.py`，包含 `from __future__ import annotations` 且仅标准库导入
- [x] 1.2 定义 `RiskLevel(StrEnum)`，包含 LOW/MEDIUM/HIGH/CRITICAL 成员
- [x] 1.3 定义 `AccessDecision(StrEnum)`，包含 EXECUTE/EXECUTE_SANDBOX/REQUIRE_APPROVAL/DENY 成员
- [x] 1.4 定义 `ApprovalScope(StrEnum)`，包含 ONCE/SESSION/PROJECT/GLOBAL 成员
- [x] 1.5 定义 `RuleAction(StrEnum)`，包含 ALLOW/DENY/ASK 成员
- [x] 1.6 定义 `ApprovalDecision` 数据类（approved: bool, reason: str = "", scope: ApprovalScope = ONCE）
- [x] 1.7 定义 `ToolRule` 数据类（action: RuleAction, pattern: str, priority: int = 0）
- [x] 1.8 定义 `ApprovalCallback` ABC，包含抽象 `async request_approval(self, tool_name, arguments, risk_level, context) -> ApprovalDecision`

## 2. 引擎层 — ToolAccessPolicy（`engine/tool_access.py`）

- [x] 2.1 定义 `ToolAccessPolicy` 具体类，包含 `evaluate(tool_meta: dict, rules: list[ToolRule], context: dict) -> AccessDecision`
- [x] 2.2 使用 `fnmatch` 实现 `_match_rules(tool_name: str, rules: list[ToolRule]) -> RuleAction | None` 用于 glob 模式匹配
- [x] 2.3 实现规则评估顺序：先 DENY，然后 ASK，然后 ALLOW，首个匹配胜出
- [x] 2.4 实现风险级别回退：LOW→EXECUTE，MEDIUM→EXECUTE 或 EXECUTE_SANDBOX，HIGH→REQUIRE_APPROVAL 或 EXECUTE_SANDBOX，CRITICAL→始终 REQUIRE_APPROVAL
- [x] 2.5 处理 `approval_required=True` 覆盖：无论风险级别如何，始终 REQUIRE_APPROVAL
- [x] 2.6 处理 `sandbox_enabled=True`：当风险允许时路由到 EXECUTE_SANDBOX 而不是 EXECUTE

## 3. 模型层 — ApprovalRecord（`models/approval.py`）

- [x] 3.1 创建 `models/approval.py`，包含 ApprovalRecordModel(BaseModel)：workspace_id, tool_name, session_id（可为空）, user_id（可为空）, scope（默认 "once"）, status（默认 "pending"）, risk_level, reason（可为空）, expires_at（可为空）
- [x] 3.2 定义 `ApprovalCreateSchema` 和 `ApprovalReadSchema` Pydantic 模式
- [x] 3.3 创建 `approval_records` 表的 Alembic 迁移

## 4. 模型层 — ToolPolicyModel（`models/tool_policy.py`）

- [x] 4.1 创建 `models/tool_policy.py`，包含 ToolPolicyModel(BaseModel)：workspace_id, rule_action, tool_pattern, priority（默认 0）, description（可为空）
- [x] 4.2 在 (workspace_id, tool_pattern, rule_action, deleted, deleted_at) 上添加唯一约束
- [x] 4.3 定义 `ToolPolicyCreateSchema` 和 `ToolPolicyReadSchema` Pydantic 模式
- [x] 4.4 创建 `tool_policies` 表的 Alembic 迁移

## 5. ToolWorker 集成（`engine/workers/tool_worker.py`）

- [x] 5.1 向 ToolWorker 构造函数添加 `access_policy: ToolAccessPolicy | None = None` 和 `approval_callback: ApprovalCallback | None = None`
- [x] 5.2 添加 `_resolve_tool_meta(tool_name: str, context: dict) -> dict` 辅助方法，从工具注册表或通道上下文查找 risk_level、approval_required、sandbox_enabled
- [x] 5.3 添加 `_check_access(tool_name, arguments, context, execution_context) -> AccessDecision | None`，当未配置策略时返回 None（向后兼容）
- [x] 5.4 在 `_execute_single_tool` 中，PreToolHook 之前：如果配置了策略，评估访问决策
- [x] 5.5 处理 DENY：返回错误工具结果消息而不执行
- [x] 5.6 处理 REQUIRE_APPROVAL：如果配置了回调，等待 `request_approval()`；如果没有，拒绝（故障关闭）
- [x] 5.7 处理 EXECUTE_SANDBOX：路由到 `port.tool_execute_sandbox()` 而不是 `port.tool_execute()`
- [x] 5.8 处理 EXECUTE：继续现有的 `port.tool_execute()` 路径

## 6. 测试 — 引擎层（`tests/test_engine/test_tool_access.py`）

- [x] 6.1 测试 RiskLevel 枚举：4 个成员，字符串值，StrEnum 比较
- [x] 6.2 测试 AccessDecision 枚举：4 个成员，字符串值
- [x] 6.3 测试 ApprovalScope 枚举：4 个成员，默认 ONCE
- [x] 6.4 测试 RuleAction 枚举：3 个成员
- [x] 6.5 测试 ApprovalDecision 数据类：approved/reason/scope 字段和默认值
- [x] 6.6 测试 ToolRule 数据类：action/pattern/priority 字段和默认值
- [x] 6.7 测试 ApprovalCallback ABC：不能实例化，子类可行
- [x] 6.8 测试 ToolAccessPolicy.evaluate — 无规则 + LOW → EXECUTE
- [x] 6.9 测试 evaluate — 无规则 + MEDIUM + sandbox_enabled → EXECUTE_SANDBOX
- [x] 6.10 测试 evaluate — 无规则 + HIGH + 无 sandbox → REQUIRE_APPROVAL
- [x] 6.11 测试 evaluate — 无规则 + CRITICAL + sandbox_enabled → REQUIRE_APPROVAL
- [x] 6.12 测试 approval_required=True 覆盖风险级别 → REQUIRE_APPROVAL
- [x] 6.13 测试 DENY 规则覆盖一切 → DENY
- [x] 6.14 测试 ALLOW 规则覆盖风险级别 → EXECUTE
- [x] 6.15 测试 ASK 规则 → REQUIRE_APPROVAL
- [x] 6.16 测试规则评估顺序：DENY > ASK > ALLOW
- [x] 6.17 测试模式匹配：精确、通配符（*）、全匹配、无匹配
- [x] 6.18 测试 ToolAccessPolicy 直接实例化（具体类，不是 ABC）

## 7. 测试 — ToolWorker 集成（`tests/test_engine/test_tool_worker.py`）

- [x] 7.1 测试向后兼容：未配置策略 → 所有工具通过 tool_execute() 执行
- [x] 7.2 测试沙箱路由：sandbox_enabled=True → 调用 tool_execute_sandbox()
- [x] 7.3 测试批准流程：REQUIRE_APPROVAL + 回调返回批准 → 工具执行
- [x] 7.4 测试批准被拒绝：REQUIRE_APPROVAL + 回调返回拒绝 → 错误消息
- [x] 7.5 测试故障关闭：REQUIRE_APPROVAL + 无回调 → 错误消息（拒绝）
- [x] 7.6 测试 DENY 决策：策略返回 DENY → 错误消息而不执行
- [x] 7.7 测试 CRITICAL 风险 + 沙箱：仍然 REQUIRE_APPROVAL（沙箱不绕过）

## 8. 测试 — 模型（`tests/test_models/test_models.py`）

- [x] 8.1 测试 ApprovalRecordModel 创建带默认值（status="pending", scope="once"）
- [x] 8.2 测试 ApprovalRecordModel 状态转换（pending→approved/rejected/expired）
- [x] 8.3 测试 ApprovalReadSchema 的 from_attributes 转换
- [x] 8.4 测试 ToolPolicyModel 创建带默认值
- [x] 8.5 测试 ToolPolicyModel 在 (workspace_id, tool_pattern, rule_action) 上的唯一约束

## 9. 文档和验证

- [x] 9.1 验证引擎层没有新的外部依赖
- [x] 9.2 运行 ruff check + ruff format --check + mypy + pytest — 全部通过
