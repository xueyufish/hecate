## ADDED Requirements — 新增需求

### Requirement: RiskLevel enum — RiskLevel 枚举
系统 SHALL 在 `engine/tool_access.py` 中将 `RiskLevel` 定义为 `StrEnum`，包含四个成员：`LOW`、`MEDIUM`、`HIGH`、`CRITICAL`。

#### Scenario: String values — 字符串值
- **WHEN** `RiskLevel.LOW` 转换为字符串
- **THEN** 值为 `"low"`

#### Scenario: Four members — 四个成员
- **WHEN** 评估 `len(list(RiskLevel))`
- **THEN** 结果为 `4`

#### Scenario: Literal string comparison — 字面字符串比较
- **WHEN** `RiskLevel.CRITICAL == "critical"`
- **THEN** 比较结果为 `True`

### Requirement: AccessDecision enum — AccessDecision 枚举
系统 SHALL 将 `AccessDecision` 定义为 `StrEnum`，包含四个成员：`EXECUTE`、`EXECUTE_SANDBOX`、`REQUIRE_APPROVAL`、`DENY`。

#### Scenario: Four decision types — 四种决策类型
- **WHEN** 评估 `len(list(AccessDecision))`
- **THEN** 结果为 `4`

#### Scenario: String values — 字符串值
- **WHEN** `AccessDecision.EXECUTE_SANDBOX` 转换为字符串
- **THEN** 值为 `"execute_sandbox"`

### Requirement: ApprovalScope enum — ApprovalScope 枚举
系统 SHALL 将 `ApprovalScope` 定义为 `StrEnum`，包含四个成员：`ONCE`、`SESSION`、`PROJECT`、`GLOBAL`。

#### Scenario: Four scopes — 四个作用域
- **WHEN** 评估 `len(list(ApprovalScope))`
- **THEN** 结果为 `4`

#### Scenario: Default scope — 默认作用域
- **WHEN** 在无显式作用域的情况下构造 `ApprovalDecision`
- **THEN** `scope` 为 `ApprovalScope.ONCE`

### Requirement: ApprovalDecision dataclass — ApprovalDecision 数据类
系统 SHALL 将 `ApprovalDecision` 定义为数据类，包含三个字段：`approved`（bool）、`reason`（str，默认 ""）和 `scope`（ApprovalScope，默认 ONCE）。

#### Scenario: Approved decision — 批准的决策
- **WHEN** 构造 `ApprovalDecision(approved=True)`
- **THEN** `approved` 为 `True`，`reason` 为 `""`，`scope` 为 `ApprovalScope.ONCE`

#### Scenario: Denied with reason — 带理由的拒绝
- **WHEN** 构造 `ApprovalDecision(approved=False, reason="Timeout")`
- **THEN** `approved` 为 `False`，`reason` 为 `"Timeout"`

### Requirement: ApprovalCallback abstract base class — ApprovalCallback 抽象基类
系统 SHALL 在 `engine/tool_access.py` 中将 `ApprovalCallback` 定义为 ABC，包含一个抽象异步方法：`request_approval(self, tool_name: str, arguments: dict, risk_level: str, context: dict) -> ApprovalDecision`。

#### Scenario: Cannot instantiate directly — 不能直接实例化
- **WHEN** 调用 `ApprovalCallback()`
- **THEN** 抛出 `TypeError`

#### Scenario: Subclass with implementation succeeds — 带实现的子类成功
- **WHEN** 一个类继承自 `ApprovalCallback` 并实现了 `request_approval`
- **THEN** 该类可以被实例化

### Requirement: ToolRule dataclass — ToolRule 数据类
系统 SHALL 将 `ToolRule` 定义为数据类，包含三个字段：`action`（RuleAction 枚举：ALLOW/DENY/ASK）、`pattern`（str，工具名称 glob）和 `priority`（int，默认 0）。

#### Scenario: Rule construction — 规则构造
- **WHEN** 构造 `ToolRule(action=RuleAction.DENY, pattern="terminal(rm *)")`
- **THEN** `action` 为 `RuleAction.DENY`，`pattern` 为 `"terminal(rm *)"`

#### Scenario: Default priority — 默认优先级
- **WHEN** 构造 `ToolRule(action=RuleAction.ALLOW, pattern="*")`
- **THEN** `priority` 为 `0`

### Requirement: RuleAction enum — RuleAction 枚举
系统 SHALL 将 `RuleAction` 定义为 `StrEnum`，包含三个成员：`ALLOW`、`DENY`、`ASK`。

#### Scenario: Three actions — 三种操作
- **WHEN** 评估 `len(list(RuleAction))`
- **THEN** 结果为 `3`

### Requirement: ToolAccessPolicy evaluate method — ToolAccessPolicy 评估方法
系统 SHALL 将 `ToolAccessPolicy` 定义为 `engine/tool_access.py` 中的具体类，具有 `evaluate(tool_meta: dict, rules: list[ToolRule], context: dict) -> AccessDecision` 方法，应用三层评估。

#### Scenario: No rules, LOW risk — auto-execute — 无规则，LOW 风险——自动执行
- **WHEN** 调用 `policy.evaluate({"risk_level": "low", "approval_required": False, "sandbox_enabled": False}, rules=[], context={})`
- **THEN** 结果为 `AccessDecision.EXECUTE`

#### Scenario: No rules, MEDIUM risk, sandbox enabled — 无规则，MEDIUM 风险，启用沙箱
- **WHEN** 调用 `policy.evaluate({"risk_level": "medium", "approval_required": False, "sandbox_enabled": True}, rules=[], context={})`
- **THEN** 结果为 `AccessDecision.EXECUTE_SANDBOX`

#### Scenario: No rules, HIGH risk, no sandbox — 无规则，HIGH 风险，无沙箱
- **WHEN** 调用 `policy.evaluate({"risk_level": "high", "approval_required": False, "sandbox_enabled": False}, rules=[], context={})`
- **THEN** 结果为 `AccessDecision.REQUIRE_APPROVAL`

#### Scenario: No rules, CRITICAL risk, sandbox enabled — 无规则，CRITICAL 风险，启用沙箱
- **WHEN** 调用 `policy.evaluate({"risk_level": "critical", "approval_required": False, "sandbox_enabled": True}, rules=[], context={})`
- **THEN** 结果为 `AccessDecision.REQUIRE_APPROVAL`

#### Scenario: approval_required overrides risk level — approval_required 覆盖风险级别
- **WHEN** 调用 `policy.evaluate({"risk_level": "low", "approval_required": True, "sandbox_enabled": False}, rules=[], context={})`
- **THEN** 结果为 `AccessDecision.REQUIRE_APPROVAL`

#### Scenario: DENY rule overrides everything — DENY 规则覆盖一切
- **WHEN** 调用 `policy.evaluate({"risk_level": "low", "approval_required": False, "sandbox_enabled": False}, rules=[ToolRule(action=RuleAction.DENY, pattern="*")], context={})`
- **THEN** 结果为 `AccessDecision.DENY`

#### Scenario: ALLOW rule overrides risk level — ALLOW 规则覆盖风险级别
- **WHEN** 调用 `policy.evaluate({"risk_level": "high", "approval_required": False, "sandbox_enabled": False}, rules=[ToolRule(action=RuleAction.ALLOW, pattern="*")], context={})`
- **THEN** 结果为 `AccessDecision.EXECUTE`

#### Scenario: ASK rule overrides risk level — ASK 规则覆盖风险级别
- **WHEN** 调用 `policy.evaluate({"risk_level": "low", "approval_required": False, "sandbox_enabled": False}, rules=[ToolRule(action=RuleAction.ASK, pattern="*")], context={})`
- **THEN** 结果为 `AccessDecision.REQUIRE_APPROVAL`

### Requirement: Rule evaluation order — 规则评估顺序
`evaluate` 方法 SHALL 按顺序检查规则：先 DENY，然后 ASK，然后 ALLOW，然后风险级别回退。第一个匹配的规则胜出。

#### Scenario: DENY takes precedence over ALLOW — DENY 优先于 ALLOW
- **WHEN** 规则包含 `ToolRule(DENY, "terminal(*)")` 和 `ToolRule(ALLOW, "terminal(git:*)")`
- **AND** 工具名称为 `"terminal(git push)"`
- **THEN** 结果为 `AccessDecision.DENY`

#### Scenario: ASK takes precedence over ALLOW — ASK 优先于 ALLOW
- **WHEN** 规则包含 `ToolRule(ASK, "write_file(.env*)")` 和 `ToolRule(ALLOW, "write_file(*)")`
- **AND** 工具名称为 `"write_file(.env.production)"`
- **THEN** 结果为 `AccessDecision.REQUIRE_APPROVAL`

#### Scenario: No rule matches — fallback to risk level — 无规则匹配——回退到风险级别
- **WHEN** 规则包含 `ToolRule(ALLOW, "terminal(git:*)")`
- **AND** 工具名称为 `"execute_python"`
- **AND** 风险级别为 `"high"`
- **THEN** 结果为 `AccessDecision.REQUIRE_APPROVAL`

### Requirement: ToolAccessPolicy pattern matching — ToolAccessPolicy 模式匹配
`evaluate` 方法 SHALL 支持使用 `fnmatch` 进行 glob 风格的工具名称模式匹配。

#### Scenario: Exact match — 精确匹配
- **WHEN** 模式为 `"terminal"`，工具名称为 `"terminal"`
- **THEN** 模式匹配

#### Scenario: Wildcard match — 通配符匹配
- **WHEN** 模式为 `"terminal(git:*)"`，工具名称为 `"terminal(git:push)"`
- **THEN** 模式匹配

#### Scenario: Catch-all match — 全匹配
- **WHEN** 模式为 `"*"`，工具名称为任意字符串
- **THEN** 模式匹配

#### Scenario: No match — 不匹配
- **WHEN** 模式为 `"terminal(rm:*)"`，工具名称为 `"terminal(git:push)"`
- **THEN** 模式不匹配

### Requirement: ToolAccessPolicy is a concrete class — ToolAccessPolicy 是具体类
`ToolAccessPolicy` SHALL 是具体类（不是 ABC），可以直接实例化，无需构造函数参数。

#### Scenario: Direct instantiation — 直接实例化
- **WHEN** 调用 `ToolAccessPolicy()`
- **THEN** 实例创建成功，无错误

### Requirement: ApprovalRecord model — ApprovalRecord 模型
系统 SHALL 在 `models/approval.py` 中定义 `ApprovalRecordModel(BaseModel)`，字段包括：`workspace_id`（UUID）、`tool_name`（String 255）、`session_id`（UUID，可为空）、`user_id`（UUID，可为空）、`scope`（String 20，默认 "once"）、`status`（String 20，默认 "pending"）、`risk_level`（String 20）、`reason`（Text，可为空）、`expires_at`（DateTime，可为空）。

#### Scenario: Create approval record — 创建批准记录
- **WHEN** 使用 workspace_id、tool_name="terminal"、risk_level="high" 创建 ApprovalRecordModel
- **THEN** `status` 为 `"pending"`，`scope` 为 `"once"`

#### Scenario: Status values — 状态值
- **WHEN** status 设置为 `"approved"`、`"rejected"` 或 `"expired"`
- **THEN** 值正确存储在数据库中

### Requirement: ApprovalRecord Pydantic schemas — ApprovalRecord Pydantic 模式
系统 SHALL 定义 `ApprovalCreateSchema` 和 `ApprovalReadSchema` Pydantic 模式用于 API 兼容性。

#### Scenario: ApprovalReadSchema from attributes — ApprovalReadSchema 从属性
- **WHEN** 调用 `ApprovalReadSchema.model_validate(approval_record_orm_instance)`
- **THEN** 所有字段从 ORM 模型正确填充

### Requirement: ToolPolicyModel for workspace rules — 用于工作空间规则的 ToolPolicyModel
系统 SHALL 在 `models/tool_policy.py` 中定义 `ToolPolicyModel(BaseModel)`，字段包括：`workspace_id`（UUID）、`rule_action`（String 20）、`tool_pattern`（String 255）、`priority`（Integer，默认 0）、`description`（String 500，可为空）。

#### Scenario: Create deny rule — 创建拒绝规则
- **WHEN** 使用 rule_action="deny"、tool_pattern="terminal(rm:*)" 创建 ToolPolicyModel
- **THEN** 规则以 priority=0 存储

#### Scenario: Unique constraint — 唯一约束
- **WHEN** 创建两个具有相同 workspace_id + tool_pattern + rule_action 的规则
- **THEN** 抛出数据库完整性错误

### Requirement: ToolWorker sandbox routing — ToolWorker 沙箱路由
ToolWorker SHALL 将 `sandbox_enabled=True` 的工具路由到 `port.tool_execute_sandbox()`，将 `sandbox_enabled=False` 的工具路由到 `port.tool_execute()`。

#### Scenario: Sandbox-enabled tool routes to sandbox executor — 启用沙箱的工具路由到沙箱执行器
- **WHEN** ToolWorker 执行 `sandbox_enabled=True` 的工具调用
- **THEN** 调用 `port.tool_execute_sandbox()` 而不是 `port.tool_execute()`

#### Scenario: Non-sandbox tool routes to normal executor — 非沙箱工具路由到正常执行器
- **WHEN** ToolWorker 执行 `sandbox_enabled=False` 的工具调用
- **THEN** 像以前一样调用 `port.tool_execute()`

#### Scenario: Sandbox does not bypass approval — 沙箱不会绕过批准
- **WHEN** 工具具有 `sandbox_enabled=True` 且 `risk_level="critical"`
- **AND** 尚未授予批准
- **THEN** 工具 NOT 被执行（REQUIRE_APPROVAL 优先）

### Requirement: ToolWorker approval enforcement — ToolWorker 批准执行
ToolWorker SHALL 在执行任何工具调用前检查 `ToolAccessPolicy.evaluate()`。如果决策是 `REQUIRE_APPROVAL`，Worker SHALL 调用 `ApprovalCallback.request_approval()`，且仅当决策为 `approved=True` 时才执行工具。

#### Scenario: Tool requires approval — approved — 工具需要批准——已批准
- **WHEN** 策略为工具调用返回 `REQUIRE_APPROVAL`
- **AND** `ApprovalCallback.request_approval()` 返回 `ApprovalDecision(approved=True)`
- **THEN** 工具正常执行

#### Scenario: Tool requires approval — denied — 工具需要批准——已拒绝
- **WHEN** 策略为工具调用返回 `REQUIRE_APPROVAL`
- **AND** `ApprovalCallback.request_approval()` 返回 `ApprovalDecision(approved=False)`
- **THEN** 工具 NOT 被执行
- **AND** 返回带有 `is_error=True` 和拒绝理由的工具结果消息

#### Scenario: Tool requires approval — timeout (fail-closed) — 工具需要批准——超时（故障关闭）
- **WHEN** 策略为工具调用返回 `REQUIRE_APPROVAL`
- **AND** 未配置批准回调（None）
- **THEN** 工具 NOT 被执行（故障关闭）
- **AND** 返回带有 `is_error=True` 和"未配置批准回调"的工具结果消息

#### Scenario: DENY decision blocks execution — DENY 决策阻止执行
- **WHEN** 策略为工具调用返回 `DENY`
- **THEN** 工具 NOT 被执行
- **AND** 返回带有 `is_error=True` 和拒绝理由的工具结果消息

### Requirement: ToolWorker constructor accepts policy and callback — ToolWorker 构造函数接受策略和回调
ToolWorker 构造函数 SHALL 接受可选的 `access_policy: ToolAccessPolicy | None` 和 `approval_callback: ApprovalCallback | None` 参数。

#### Scenario: Default constructor (backward compatible) — 默认构造函数（向后兼容）
- **WHEN** 在无 access_policy 或 approval_callback 的情况下构造 `ToolWorker(port=port)`
- **THEN** 所有工具像以前一样执行（无执行，向后兼容）

#### Scenario: With policy but no callback — 有策略但无回调
- **WHEN** 在无 approval_callback 的情况下构造 `ToolWorker(port=port, access_policy=policy)`
- **AND** 遇到 `REQUIRE_APPROVAL` 决策的工具
- **THEN** 工具被拒绝（故障关闭，无法询问）

### Requirement: ApprovalScope caching — ApprovalScope 缓存
当批准以 `scope=SESSION` 授予时，ApprovalCallback 实现 SHALL 将决策以 `(session_id, tool_name)` 为键缓存在内存中。同一会话中同一工具的后续调用 SHALL 返回缓存的决策而不阻塞。

#### Scenario: SESSION scope caches within session — SESSION 作用域在会话内缓存
- **WHEN** 批准以 `scope=SESSION` 授予，用于会话 "s1" 中的工具 "terminal"
- **AND** 在会话 "s1" 中再次调用同一工具
- **THEN** 返回缓存的批准，无需新的阻塞调用

#### Scenario: ONCE scope does not cache — ONCE 作用域不缓存
- **WHEN** 批准以 `scope=ONCE` 授予，用于工具 "terminal"
- **AND** 再次调用同一工具
- **THEN** 进行新的 `request_approval()` 调用

### Requirement: Backward compatibility — 向后兼容
当 ToolWorker 上未配置 `access_policy` 时，所有工具 SHALL 像以前一样通过 `port.tool_execute()` 执行——无执行、无沙箱路由、无批准检查。

#### Scenario: No policy — existing behavior unchanged — 无策略——现有行为不变
- **WHEN** ToolWorker 在无 `access_policy` 的情况下构造
- **AND** 处理 `risk_level="critical"` 和 `approval_required=True` 的工具调用
- **THEN** 工具通过 `port.tool_execute()` 正常执行

### Requirement: Engine layer zero dependencies — 引擎层零依赖
`engine/tool_access.py` SHALL 除了 Python 标准库外，没有外部依赖。不从 `models/`、`services/`、`api/` 或第三方包导入。

#### Scenario: No model imports — 无模型导入
- **WHEN** 检查 `engine/tool_access.py`
- **THEN** 没有引用 `hecate.models`、`hecate.services` 或 `hecate.api` 的导入语句

#### Scenario: Only stdlib imports — 仅标准库导入
- **WHEN** 检查 `engine/tool_access.py` 的导入
- **THEN** 所有导入来自 `__future__`、`abc`、`dataclasses`、`enum`、`fnmatch`、`logging` 或 `typing`
