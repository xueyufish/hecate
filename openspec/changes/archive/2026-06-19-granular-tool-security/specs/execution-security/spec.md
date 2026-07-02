## MODIFIED Requirements

### Requirement: ToolRule dataclass
The system SHALL define `ToolRule` as a dataclass with four fields: `action` (RuleAction enum: ALLOW/DENY/ASK), `pattern` (str, tool-name glob), `priority` (int, default 0), and `arg_conditions` (dict[str, str] | None, default None). When `arg_conditions` is set, the rule matches only if the tool name matches AND all argument conditions match their corresponding argument values via `fnmatch`.

#### Scenario: Rule construction
- **WHEN** `ToolRule(action=RuleAction.DENY, pattern="terminal(rm *)")` is constructed
- **THEN** `action` is `RuleAction.DENY`, `pattern` is `"terminal(rm *)"`, and `arg_conditions` is `None`

#### Scenario: Default priority
- **WHEN** `ToolRule(action=RuleAction.ALLOW, pattern="*")` is constructed
- **THEN** `priority` is `0` and `arg_conditions` is `None`

#### Scenario: Rule with arg_conditions
- **WHEN** `ToolRule(action=RuleAction.ASK, pattern="write_file", arg_conditions={"path": "*.env"})` is constructed
- **THEN** `arg_conditions` is `{"path": "*.env"}`

### Requirement: ToolAccessPolicy evaluate method
The system SHALL define `ToolAccessPolicy` as a concrete class in `engine/tool_access.py` with an `evaluate(tool_meta: dict, rules: list[ToolRule], context: dict, arguments: dict | None = None) -> AccessDecision` method that applies five-layer evaluation: dangerous patterns, user rules (with arg_conditions), workspace boundary, risk-level fallback, and sandbox routing.

#### Scenario: No rules, LOW risk — auto-execute
- **WHEN** `policy.evaluate({"risk_level": "low", "approval_required": False, "sandbox_enabled": False}, rules=[], context={})` is called
- **THEN** the result is `AccessDecision.EXECUTE`

#### Scenario: No rules, MEDIUM risk, sandbox enabled
- **WHEN** `policy.evaluate({"risk_level": "medium", "approval_required": False, "sandbox_enabled": True}, rules=[], context={})` is called
- **THEN** the result is `AccessDecision.EXECUTE_SANDBOX`

#### Scenario: No rules, HIGH risk, no sandbox
- **WHEN** `policy.evaluate({"risk_level": "high", "approval_required": False, "sandbox_enabled": False}, rules=[], context={})` is called
- **THEN** the result is `AccessDecision.REQUIRE_APPROVAL`

#### Scenario: No rules, CRITICAL risk, sandbox enabled
- **WHEN** `policy.evaluate({"risk_level": "critical", "approval_required": False, "sandbox_enabled": True}, rules=[], context={})` is called
- **THEN** the result is `AccessDecision.REQUIRE_APPROVAL`

#### Scenario: approval_required overrides risk level
- **WHEN** `policy.evaluate({"risk_level": "low", "approval_required": True, "sandbox_enabled": False}, rules=[], context={})` is called
- **THEN** the result is `AccessDecision.REQUIRE_APPROVAL`

#### Scenario: DENY rule overrides everything
- **WHEN** `policy.evaluate({"risk_level": "low", "approval_required": False, "sandbox_enabled": False}, rules=[ToolRule(action=RuleAction.DENY, pattern="*")], context={})` is called
- **THEN** the result is `AccessDecision.DENY`

#### Scenario: ALLOW rule overrides risk level
- **WHEN** `policy.evaluate({"risk_level": "high", "approval_required": False, "sandbox_enabled": False}, rules=[ToolRule(action=RuleAction.ALLOW, pattern="*")], context={})` is called
- **THEN** the result is `AccessDecision.EXECUTE`

#### Scenario: ASK rule overrides risk level
- **WHEN** `policy.evaluate({"risk_level": "low", "approval_required": False, "sandbox_enabled": False}, rules=[ToolRule(action=RuleAction.ASK, pattern="*")], context={})` is called
- **THEN** the result is `AccessDecision.REQUIRE_APPROVAL`

#### Scenario: Dangerous pattern overrides user ALLOW
- **WHEN** `policy.evaluate({"risk_level": "low", "name": "bash"}, rules=[ToolRule(ALLOW, "bash")], context={"tool_name": "bash"}, arguments={"command": "rm -rf /"})` is called
- **THEN** the result is `AccessDecision.DENY`

#### Scenario: arg_conditions match produces decision
- **WHEN** `policy.evaluate({"name": "write_file"}, rules=[ToolRule(ASK, "write_file", arg_conditions={"path": "*.env"})], context={"tool_name": "write_file"}, arguments={"path": ".env"})` is called
- **THEN** the result is `AccessDecision.REQUIRE_APPROVAL`

#### Scenario: arg_conditions do not match — fallthrough
- **WHEN** `policy.evaluate({"risk_level": "low", "name": "write_file"}, rules=[ToolRule(ASK, "write_file", arg_conditions={"path": "*.env"})], context={"tool_name": "write_file"}, arguments={"path": "output.txt"})` is called
- **THEN** the arg_conditions rule does not match and result is `AccessDecision.EXECUTE` (risk-level fallback)

### Requirement: Rule evaluation order
The `evaluate` method SHALL check rules in order: DENY first, then ASK, then ALLOW. Within each tier, rules with `arg_conditions` that match are checked alongside name-only rules, sorted by priority (highest first). The first matching rule wins. Dangerous patterns are checked before user rules.

#### Scenario: DENY takes precedence over ALLOW
- **WHEN** rules contain both `ToolRule(DENY, "terminal(*)")` and `ToolRule(ALLOW, "terminal(git:*)")`
- **AND** tool name is `"terminal(git push)"`
- **THEN** the result is `AccessDecision.DENY`

#### Scenario: ASK takes precedence over ALLOW
- **WHEN** rules contain both `ToolRule(ASK, "write_file", arg_conditions={"path": ".env*"})` and `ToolRule(ALLOW, "write_file")`
- **AND** tool name is `"write_file"` and arguments are `{"path": ".env.production"}`
- **THEN** the result is `AccessDecision.REQUIRE_APPROVAL`

#### Scenario: No rule matches — fallback to risk level
- **WHEN** rules contain `ToolRule(ALLOW, "terminal(git:*)")`
- **AND** tool name is `"execute_python"`
- **AND** risk level is `"high"`
- **THEN** the result is `AccessDecision.REQUIRE_APPROVAL`

#### Scenario: arg_conditions rule matches before name-only rule in same tier
- **WHEN** rules contain `ToolRule(DENY, "write_file", arg_conditions={"path": "*.env"}, priority=10)` and `ToolRule(ALLOW, "write_file", priority=0)`
- **AND** tool name is `"write_file"` and arguments are `{"path": "config.env"}`
- **THEN** the DENY rule matches first (higher priority in DENY tier) and result is `AccessDecision.DENY`
