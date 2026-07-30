## MODIFIED Requirements — 修改的需求

### Requirement: ToolRule dataclass — ToolRule 数据类
系统 SHALL 将 `ToolRule` 定义为数据类，包含四个字段：`action`（RuleAction 枚举：ALLOW/DENY/ASK）、`pattern`（str，工具名称 glob）、`priority`（int，默认 0）和 `arg_conditions`（dict[str, str] | None，默认 None）。当设置了 `arg_conditions` 时，仅当工具名称匹配且所有参数条件通过 `fnmatch` 匹配其对应参数值时，规则才匹配。

#### Scenario: Rule construction — 场景：规则构造
- **WHEN** 构造 `ToolRule(action=RuleAction.DENY, pattern="terminal(rm *)")`
- **THEN** `action` 为 `RuleAction.DENY`，`pattern` 为 `"terminal(rm *)"`，`arg_conditions` 为 `None`

#### Scenario: Default priority — 场景：默认优先级
- **WHEN** 构造 `ToolRule(action=RuleAction.ALLOW, pattern="*")`
- **THEN** `priority` 为 `0`，`arg_conditions` 为 `None`

#### Scenario: Rule with arg_conditions — 场景：带 arg_conditions 的规则
- **WHEN** 构造 `ToolRule(action=RuleAction.ASK, pattern="write_file", arg_conditions={"path": "*.env"})`
- **THEN** `arg_conditions` 为 `{"path": "*.env"}`

### Requirement: ToolAccessPolicy evaluate method — ToolAccessPolicy 评估方法
系统 SHALL 将 `ToolAccessPolicy` 定义为 `engine/tool_access.py` 中的具体类，具有 `evaluate(tool_meta: dict, rules: list[ToolRule], context: dict, arguments: dict | None = None) -> AccessDecision` 方法，应用五层评估：危险模式、用户规则（带 arg_conditions）、工作空间边界、风险级别回退和沙箱路由。

#### Scenario: No rules, LOW risk — auto-execute — 场景：无规则，LOW 风险——自动执行
- **WHEN** 调用 `policy.evaluate({"risk_level": "low", "approval_required": False, "sandbox_enabled": False}, rules=[], context={})`
- **THEN** 结果为 `AccessDecision.EXECUTE`

#### Scenario: No rules, MEDIUM risk, sandbox enabled — 场景：无规则，MEDIUM 风险，启用沙箱
- **WHEN** 调用 `policy.evaluate({"risk_level": "medium", "approval_required": False, "sandbox_enabled": True}, rules=[], context={})`
- **THEN** 结果为 `AccessDecision.EXECUTE_SANDBOX`

#### Scenario: No rules, HIGH risk, no sandbox — 场景：无规则，HIGH 风险，无沙箱
- **WHEN** 调用 `policy.evaluate({"risk_level": "high", "approval_required": False, "sandbox_enabled": False}, rules=[], context={})`
- **THEN** 结果为 `AccessDecision.REQUIRE_APPROVAL`

#### Scenario: No rules, CRITICAL risk, sandbox enabled — 场景：无规则，CRITICAL 风险，启用沙箱
- **WHEN** 调用 `policy.evaluate({"risk_level": "critical", "approval_required": False, "sandbox_enabled": True}, rules=[], context={})`
- **THEN** 结果为 `AccessDecision.REQUIRE_APPROVAL`

#### Scenario: approval_required overrides risk level — 场景：approval_required 覆盖风险级别
- **WHEN** 调用 `policy.evaluate({"risk_level": "low", "approval_required": True, "sandbox_enabled": False}, rules=[], context={})`
- **THEN** 结果为 `AccessDecision.REQUIRE_APPROVAL`

#### Scenario: DENY rule overrides everything — 场景：DENY 规则覆盖一切
- **WHEN** 调用 `policy.evaluate({"risk_level": "low", "approval_required": False, "sandbox_enabled": False}, rules=[ToolRule(action=RuleAction.DENY, pattern="*")], context={})`
- **THEN** 结果为 `AccessDecision.DENY`

#### Scenario: ALLOW rule overrides risk level — 场景：ALLOW 规则覆盖风险级别
- **WHEN** 调用 `policy.evaluate({"risk_level": "high", "approval_required": False, "sandbox_enabled": False}, rules=[ToolRule(action=RuleAction.ALLOW, pattern="*")], context={})`
- **THEN** 结果为 `AccessDecision.EXECUTE`

#### Scenario: ASK rule overrides risk level — 场景：ASK 规则覆盖风险级别
- **WHEN** 调用 `policy.evaluate({"risk_level": "low", "approval_required": False, "sandbox_enabled": False}, rules=[ToolRule(action=RuleAction.ASK, pattern="*")], context={})`
- **THEN** 结果为 `AccessDecision.REQUIRE_APPROVAL`

#### Scenario: Dangerous pattern overrides user ALLOW — 场景：危险模式覆盖用户 ALLOW
- **WHEN** 调用 `policy.evaluate({"risk_level": "low", "name": "bash"}, rules=[ToolRule(ALLOW, "bash")], context={"tool_name": "bash"}, arguments={"command": "rm -rf /"})`
- **THEN** 结果为 `AccessDecision.DENY`

#### Scenario: arg_conditions match produces decision — 场景：arg_conditions 匹配产生决策
- **WHEN** 调用 `policy.evaluate({"name": "write_file"}, rules=[ToolRule(ASK, "write_file", arg_conditions={"path": "*.env"})], context={"tool_name": "write_file"}, arguments={"path": ".env"})`
- **THEN** 结果为 `AccessDecision.REQUIRE_APPROVAL`

#### Scenario: arg_conditions do not match — fallthrough — 场景：arg_conditions 不匹配——穿透
- **WHEN** 调用 `policy.evaluate({"risk_level": "low", "name": "write_file"}, rules=[ToolRule(ASK, "write_file", arg_conditions={"path": "*.env"})], context={"tool_name": "write_file"}, arguments={"path": "output.txt"})`
- **THEN** arg_conditions 规则不匹配，结果为 `AccessDecision.EXECUTE`（风险级别回退）

### Requirement: Rule evaluation order — 规则评估顺序
`evaluate` 方法 SHALL 按顺序检查规则：先 DENY，然后 ASK，然后 ALLOW。在每个层级内，匹配的带 `arg_conditions` 的规则与仅名称规则一起检查，按优先级（最高优先）排序。第一个匹配的规则胜出。危险模式在用户规则之前检查。

#### Scenario: DENY takes precedence over ALLOW — 场景：DENY 优先于 ALLOW
- **WHEN** 规则包含 `ToolRule(DENY, "terminal(*)")` 和 `ToolRule(ALLOW, "terminal(git:*)")`
- **AND** 工具名称为 `"terminal(git push)"`
- **THEN** 结果为 `AccessDecision.DENY`

#### Scenario: ASK takes precedence over ALLOW — 场景：ASK 优先于 ALLOW
- **WHEN** 规则包含 `ToolRule(ASK, "write_file", arg_conditions={"path": ".env*"})` 和 `ToolRule(ALLOW, "write_file")`
- **AND** 工具名称为 `"write_file"`，参数为 `{"path": ".env.production"}`
- **THEN** 结果为 `AccessDecision.REQUIRE_APPROVAL`

#### Scenario: No rule matches — fallback to risk level — 场景：无规则匹配——回退到风险级别
- **WHEN** 规则包含 `ToolRule(ALLOW, "terminal(git:*)")`
- **AND** 工具名称为 `"execute_python"`
- **AND** 风险级别为 `"high"`
- **THEN** 结果为 `AccessDecision.REQUIRE_APPROVAL`

#### Scenario: arg_conditions rule matches before name-only rule in same tier — 场景：同一层级中 arg_conditions 规则在仅名称规则前匹配
- **WHEN** 规则包含 `ToolRule(DENY, "write_file", arg_conditions={"path": "*.env"}, priority=10)` 和 `ToolRule(ALLOW, "write_file", priority=0)`
- **AND** 工具名称为 `"write_file"`，参数为 `{"path": "config.env"}`
- **THEN** DENY 规则首先匹配（DENY 层级中的更高优先级），结果为 `AccessDecision.DENY`
