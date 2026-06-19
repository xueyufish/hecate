## ADDED Requirements

### Requirement: RiskLevel enum
The system SHALL define `RiskLevel` as a `StrEnum` in `engine/tool_access.py` with four members: `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`.

#### Scenario: String values
- **WHEN** `RiskLevel.LOW` is converted to string
- **THEN** the value is `"low"`

#### Scenario: Four members
- **WHEN** `len(list(RiskLevel))` is evaluated
- **THEN** the result is `4`

#### Scenario: Literal string comparison
- **WHEN** `RiskLevel.CRITICAL == "critical"`
- **THEN** the comparison evaluates to `True`

### Requirement: AccessDecision enum
The system SHALL define `AccessDecision` as a `StrEnum` with four members: `EXECUTE`, `EXECUTE_SANDBOX`, `REQUIRE_APPROVAL`, `DENY`.

#### Scenario: Four decision types
- **WHEN** `len(list(AccessDecision))` is evaluated
- **THEN** the result is `4`

#### Scenario: String values
- **WHEN** `AccessDecision.EXECUTE_SANDBOX` is converted to string
- **THEN** the value is `"execute_sandbox"`

### Requirement: ApprovalScope enum
The system SHALL define `ApprovalScope` as a `StrEnum` with four members: `ONCE`, `SESSION`, `PROJECT`, `GLOBAL`.

#### Scenario: Four scopes
- **WHEN** `len(list(ApprovalScope))` is evaluated
- **THEN** the result is `4`

#### Scenario: Default scope
- **WHEN** an `ApprovalDecision` is constructed without explicit scope
- **THEN** `scope` is `ApprovalScope.ONCE`

### Requirement: ApprovalDecision dataclass
The system SHALL define `ApprovalDecision` as a dataclass with three fields: `approved` (bool), `reason` (str, default ""), and `scope` (ApprovalScope, default ONCE).

#### Scenario: Approved decision
- **WHEN** `ApprovalDecision(approved=True)` is constructed
- **THEN** `approved` is `True`, `reason` is `""`, and `scope` is `ApprovalScope.ONCE`

#### Scenario: Denied with reason
- **WHEN** `ApprovalDecision(approved=False, reason="Timeout")` is constructed
- **THEN** `approved` is `False` and `reason` is `"Timeout"`

### Requirement: ApprovalCallback abstract base class
The system SHALL define `ApprovalCallback` as an ABC in `engine/tool_access.py` with one abstract async method: `request_approval(self, tool_name: str, arguments: dict, risk_level: str, context: dict) -> ApprovalDecision`.

#### Scenario: Cannot instantiate directly
- **WHEN** `ApprovalCallback()` is called
- **THEN** `TypeError` is raised

#### Scenario: Subclass with implementation succeeds
- **WHEN** a class inherits from `ApprovalCallback` and implements `request_approval`
- **THEN** the class can be instantiated

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

### Requirement: RuleAction enum
The system SHALL define `RuleAction` as a `StrEnum` with three members: `ALLOW`, `DENY`, `ASK`.

#### Scenario: Three actions
- **WHEN** `len(list(RuleAction))` is evaluated
- **THEN** the result is `3`

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

### Requirement: ToolAccessPolicy pattern matching
The `evaluate` method SHALL support glob-style pattern matching for tool names using `fnmatch`.

#### Scenario: Exact match
- **WHEN** pattern is `"terminal"` and tool name is `"terminal"`
- **THEN** the pattern matches

#### Scenario: Wildcard match
- **WHEN** pattern is `"terminal(git:*)"` and tool name is `"terminal(git:push)"`
- **THEN** the pattern matches

#### Scenario: Catch-all match
- **WHEN** pattern is `"*"` and tool name is any string
- **THEN** the pattern matches

#### Scenario: No match
- **WHEN** pattern is `"terminal(rm:*)"` and tool name is `"terminal(git:push)"`
- **THEN** the pattern does not match

### Requirement: ToolAccessPolicy is a concrete class
`ToolAccessPolicy` SHALL be a concrete class (not an ABC) that can be instantiated directly with no constructor arguments.

#### Scenario: Direct instantiation
- **WHEN** `ToolAccessPolicy()` is called
- **THEN** an instance is created without error

### Requirement: ApprovalRecord model
The system SHALL define `ApprovalRecordModel(BaseModel)` in `models/approval.py` with fields: `workspace_id` (UUID), `tool_name` (String 255), `session_id` (UUID, nullable), `user_id` (UUID, nullable), `scope` (String 20, default "once"), `status` (String 20, default "pending"), `risk_level` (String 20), `reason` (Text, nullable), `expires_at` (DateTime, nullable).

#### Scenario: Create approval record
- **WHEN** an ApprovalRecordModel is created with workspace_id, tool_name="terminal", risk_level="high"
- **THEN** `status` is `"pending"` and `scope` is `"once"`

#### Scenario: Status values
- **WHEN** status is set to `"approved"`, `"rejected"`, or `"expired"`
- **THEN** the value is stored correctly in the database

### Requirement: ApprovalRecord Pydantic schemas
The system SHALL define `ApprovalCreateSchema` and `ApprovalReadSchema` Pydantic schemas for API compatibility.

#### Scenario: ApprovalReadSchema from attributes
- **WHEN** `ApprovalReadSchema.model_validate(approval_record_orm_instance)` is called
- **THEN** all fields are populated correctly from the ORM model

### Requirement: ToolPolicyModel for workspace rules
The system SHALL define `ToolPolicyModel(BaseModel)` in `models/tool_policy.py` with fields: `workspace_id` (UUID), `rule_action` (String 20), `tool_pattern` (String 255), `priority` (Integer, default 0), `description` (String 500, nullable).

#### Scenario: Create deny rule
- **WHEN** a ToolPolicyModel is created with rule_action="deny", tool_pattern="terminal(rm:*)"
- **THEN** the rule is stored with priority=0

#### Scenario: Unique constraint
- **WHEN** two rules with the same workspace_id + tool_pattern + rule_action are created
- **THEN** a database integrity error is raised

### Requirement: ToolWorker sandbox routing
ToolWorker SHALL route tools with `sandbox_enabled=True` to `port.tool_execute_sandbox()` and tools with `sandbox_enabled=False` to `port.tool_execute()`.

#### Scenario: Sandbox-enabled tool routes to sandbox executor
- **WHEN** ToolWorker executes a tool call with `sandbox_enabled=True`
- **THEN** `port.tool_execute_sandbox()` is called instead of `port.tool_execute()`

#### Scenario: Non-sandbox tool routes to normal executor
- **WHEN** ToolWorker executes a tool call with `sandbox_enabled=False`
- **THEN** `port.tool_execute()` is called as before

#### Scenario: Sandbox does not bypass approval
- **WHEN** a tool has `sandbox_enabled=True` and `risk_level="critical"`
- **AND** no approval has been granted
- **THEN** the tool is NOT executed (REQUIRE_APPROVAL takes precedence)

### Requirement: ToolWorker approval enforcement
ToolWorker SHALL check `ToolAccessPolicy.evaluate()` before executing any tool call. If the decision is `REQUIRE_APPROVAL`, the worker SHALL call `ApprovalCallback.request_approval()` and only execute the tool if the decision is `approved=True`.

#### Scenario: Tool requires approval — approved
- **WHEN** policy returns `REQUIRE_APPROVAL` for a tool call
- **AND** `ApprovalCallback.request_approval()` returns `ApprovalDecision(approved=True)`
- **THEN** the tool is executed normally

#### Scenario: Tool requires approval — denied
- **WHEN** policy returns `REQUIRE_APPROVAL` for a tool call
- **AND** `ApprovalCallback.request_approval()` returns `ApprovalDecision(approved=False)`
- **THEN** the tool is NOT executed
- **AND** a tool result message with `is_error=True` and rejection reason is returned

#### Scenario: Tool requires approval — timeout (fail-closed)
- **WHEN** policy returns `REQUIRE_APPROVAL` for a tool call
- **AND** no approval callback is configured (None)
- **THEN** the tool is NOT executed (fail-closed)
- **AND** a tool result message with `is_error=True` and "no approval callback configured" is returned

#### Scenario: DENY decision blocks execution
- **WHEN** policy returns `DENY` for a tool call
- **THEN** the tool is NOT executed
- **AND** a tool result message with `is_error=True` and deny reason is returned

### Requirement: ToolWorker constructor accepts policy and callback
ToolWorker constructor SHALL accept optional `access_policy: ToolAccessPolicy | None` and `approval_callback: ApprovalCallback | None` parameters.

#### Scenario: Default constructor (backward compatible)
- **WHEN** `ToolWorker(port=port)` is constructed without access_policy or approval_callback
- **THEN** all tools execute as before (no enforcement, backward compatible)

#### Scenario: With policy but no callback
- **WHEN** `ToolWorker(port=port, access_policy=policy)` is constructed without approval_callback
- **AND** a tool with `REQUIRE_APPROVAL` decision is encountered
- **THEN** the tool is denied (fail-closed, no callback to ask)

### Requirement: ApprovalScope caching
When an approval is granted with `scope=SESSION`, the ApprovalCallback implementation SHALL cache the decision in-memory keyed by `(session_id, tool_name)`. Subsequent calls for the same tool in the same session SHALL return the cached decision without blocking.

#### Scenario: SESSION scope caches within session
- **WHEN** approval is granted with `scope=SESSION` for tool "terminal" in session "s1"
- **AND** the same tool is called again in session "s1"
- **THEN** the cached approval is returned without a new blocking call

#### Scenario: ONCE scope does not cache
- **WHEN** approval is granted with `scope=ONCE` for tool "terminal"
- **AND** the same tool is called again
- **THEN** a new `request_approval()` call is made

### Requirement: Backward compatibility
When no `access_policy` is configured on ToolWorker, all tools SHALL execute via `port.tool_execute()` as before — no enforcement, no sandbox routing, no approval checks.

#### Scenario: No policy — existing behavior unchanged
- **WHEN** ToolWorker is constructed without `access_policy`
- **AND** a tool call with `risk_level="critical"` and `approval_required=True` is processed
- **THEN** the tool executes normally via `port.tool_execute()`

### Requirement: Engine layer zero dependencies
`engine/tool_access.py` SHALL have zero external dependencies beyond the Python standard library. No imports from `models/`, `services/`, `api/`, or third-party packages.

#### Scenario: No model imports
- **WHEN** `engine/tool_access.py` is inspected
- **THEN** no import statements reference `hecate.models`, `hecate.services`, or `hecate.api`

#### Scenario: Only stdlib imports
- **WHEN** `engine/tool_access.py` imports are inspected
- **THEN** all imports are from `__future__`, `abc`, `dataclasses`, `enum`, `fnmatch`, `logging`, or `typing`
