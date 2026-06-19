## 1. Engine Layer — Enums and Dataclasses (`engine/tool_access.py`)

- [x] 1.1 Create `engine/tool_access.py` with `from __future__ import annotations` and stdlib-only imports
- [x] 1.2 Define `RiskLevel(StrEnum)` with LOW/MEDIUM/HIGH/CRITICAL members
- [x] 1.3 Define `AccessDecision(StrEnum)` with EXECUTE/EXECUTE_SANDBOX/REQUIRE_APPROVAL/DENY members
- [x] 1.4 Define `ApprovalScope(StrEnum)` with ONCE/SESSION/PROJECT/GLOBAL members
- [x] 1.5 Define `RuleAction(StrEnum)` with ALLOW/DENY/ASK members
- [x] 1.6 Define `ApprovalDecision` dataclass (approved: bool, reason: str = "", scope: ApprovalScope = ONCE)
- [x] 1.7 Define `ToolRule` dataclass (action: RuleAction, pattern: str, priority: int = 0)
- [x] 1.8 Define `ApprovalCallback` ABC with abstract `async request_approval(self, tool_name, arguments, risk_level, context) -> ApprovalDecision`

## 2. Engine Layer — ToolAccessPolicy (`engine/tool_access.py`)

- [x] 2.1 Define `ToolAccessPolicy` concrete class with `evaluate(tool_meta: dict, rules: list[ToolRule], context: dict) -> AccessDecision`
- [x] 2.2 Implement `_match_rules(tool_name: str, rules: list[ToolRule]) -> RuleAction | None` using `fnmatch` for glob pattern matching
- [x] 2.3 Implement rule evaluation order: DENY first, then ASK, then ALLOW, first match wins
- [x] 2.4 Implement risk-level fallback: LOW→EXECUTE, MEDIUM→EXECUTE or EXECUTE_SANDBOX, HIGH→REQUIRE_APPROVAL or EXECUTE_SANDBOX, CRITICAL→REQUIRE_APPROVAL always
- [x] 2.5 Handle `approval_required=True` override: always REQUIRE_APPROVAL regardless of risk level
- [x] 2.6 Handle `sandbox_enabled=True`: route to EXECUTE_SANDBOX instead of EXECUTE when risk allows

## 3. Models Layer — ApprovalRecord (`models/approval.py`)

- [x] 3.1 Create `models/approval.py` with ApprovalRecordModel(BaseModel): workspace_id, tool_name, session_id (nullable), user_id (nullable), scope (default "once"), status (default "pending"), risk_level, reason (nullable), expires_at (nullable)
- [x] 3.2 Define `ApprovalCreateSchema` and `ApprovalReadSchema` Pydantic schemas
- [x] 3.3 Create Alembic migration for `approval_records` table

## 4. Models Layer — ToolPolicyModel (`models/tool_policy.py`)

- [x] 4.1 Create `models/tool_policy.py` with ToolPolicyModel(BaseModel): workspace_id, rule_action, tool_pattern, priority (default 0), description (nullable)
- [x] 4.2 Add unique constraint on (workspace_id, tool_pattern, rule_action, deleted, deleted_at)
- [x] 4.3 Define `ToolPolicyCreateSchema` and `ToolPolicyReadSchema` Pydantic schemas
- [x] 4.4 Create Alembic migration for `tool_policies` table

## 5. ToolWorker Integration (`engine/workers/tool_worker.py`)

- [x] 5.1 Add `access_policy: ToolAccessPolicy | None = None` and `approval_callback: ApprovalCallback | None = None` to ToolWorker constructor
- [x] 5.2 Add `_resolve_tool_meta(tool_name: str, context: dict) -> dict` helper to look up risk_level, approval_required, sandbox_enabled from tool registry or channel context
- [x] 5.3 Add `_check_access(tool_name, arguments, context, execution_context) -> AccessDecision | None` that returns None when no policy is configured (backward compat)
- [x] 5.4 In `_execute_single_tool`, before PreToolHook: if policy configured, evaluate access decision
- [x] 5.5 Handle DENY: return error tool result message without executing
- [x] 5.6 Handle REQUIRE_APPROVAL: if callback configured, await `request_approval()`; if not, deny (fail-closed)
- [x] 5.7 Handle EXECUTE_SANDBOX: route to `port.tool_execute_sandbox()` instead of `port.tool_execute()`
- [x] 5.8 Handle EXECUTE: proceed to existing `port.tool_execute()` path

## 6. Tests — Engine Layer (`tests/test_engine/test_tool_access.py`)

- [x] 6.1 Test RiskLevel enum: 4 members, string values, StrEnum comparison
- [x] 6.2 Test AccessDecision enum: 4 members, string values
- [x] 6.3 Test ApprovalScope enum: 4 members, default ONCE
- [x] 6.4 Test RuleAction enum: 3 members
- [x] 6.5 Test ApprovalDecision dataclass: approved/reason/scope fields and defaults
- [x] 6.6 Test ToolRule dataclass: action/pattern/priority fields and defaults
- [x] 6.7 Test ApprovalCallback ABC: cannot instantiate, subclass works
- [x] 6.8 Test ToolAccessPolicy.evaluate — no rules + LOW → EXECUTE
- [x] 6.9 Test evaluate — no rules + MEDIUM + sandbox_enabled → EXECUTE_SANDBOX
- [x] 6.10 Test evaluate — no rules + HIGH + no sandbox → REQUIRE_APPROVAL
- [x] 6.11 Test evaluate — no rules + CRITICAL + sandbox_enabled → REQUIRE_APPROVAL
- [x] 6.12 Test evaluate — approval_required=True overrides risk level → REQUIRE_APPROVAL
- [x] 6.13 Test evaluate — DENY rule overrides everything → DENY
- [x] 6.14 Test evaluate — ALLOW rule overrides risk level → EXECUTE
- [x] 6.15 Test evaluate — ASK rule → REQUIRE_APPROVAL
- [x] 6.16 Test rule evaluation order: DENY > ASK > ALLOW
- [x] 6.17 Test pattern matching: exact, wildcard (*), catch-all, no-match
- [x] 6.18 Test ToolAccessPolicy direct instantiation (concrete class, not ABC)

## 7. Tests — ToolWorker Integration (`tests/test_engine/test_tool_worker.py`)

- [x] 7.1 Test backward compat: no policy configured → all tools execute via tool_execute()
- [x] 7.2 Test sandbox routing: sandbox_enabled=True → tool_execute_sandbox() called
- [x] 7.3 Test approval flow: REQUIRE_APPROVAL + callback returns approved → tool executes
- [x] 7.4 Test approval denied: REQUIRE_APPROVAL + callback returns denied → error message
- [x] 7.5 Test fail-closed: REQUIRE_APPROVAL + no callback → error message (deny)
- [x] 7.6 Test DENY decision: policy returns DENY → error message without execution
- [x] 7.7 Test CRITICAL risk + sandbox: still REQUIRE_APPROVAL (sandbox doesn't bypass)

## 8. Tests — Models (`tests/test_models/test_models.py`)

- [x] 8.1 Test ApprovalRecordModel create with defaults (status="pending", scope="once")
- [x] 8.2 Test ApprovalRecordModel status transitions (pending→approved/rejected/expired)
- [x] 8.3 Test ApprovalReadSchema from_attributes conversion
- [x] 8.4 Test ToolPolicyModel create with defaults
- [x] 8.5 Test ToolPolicyModel unique constraint on (workspace_id, tool_pattern, rule_action)

## 9. Documentation and Verification

- [x] 9.1 Verify engine layer has zero new external dependencies
- [x] 9.2 Run ruff check + ruff format --check + mypy + pytest — all must pass
