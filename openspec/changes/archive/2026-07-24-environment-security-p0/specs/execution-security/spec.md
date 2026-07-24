## MODIFIED Requirements

### Requirement: ToolWorker sandbox routing
ToolWorker SHALL route tools based on `ToolAccessPolicy.evaluate()` decision. When `AGENT_ENV_SANDBOX_ENFORCEMENT=true` and the decision is `EXECUTE_SANDBOX`, ToolWorker SHALL route shell/exec tools and `sandbox_enabled=True` MCP tools to `DockerEnvironment.exec_shell()` for container-isolated execution. Python built-in tools with `EXECUTE_SANDBOX` decision SHALL execute directly (governed by WorkspaceBoundaryPolicy). When `AGENT_ENV_SANDBOX_ENFORCEMENT=false` (default), `EXECUTE_SANDBOX` is treated as `EXECUTE` (backward compatible).

#### Scenario: Sandbox-enabled tool routes to sandbox executor
- **WHEN** ToolWorker executes a tool call with `sandbox_enabled=True` and `AGENT_ENV_SANDBOX_ENFORCEMENT=false`
- **THEN** `port.tool_execute_sandbox()` is called (existing behavior preserved)

#### Scenario: EXECUTE_SANDBOX routes shell tool to DockerEnvironment
- **WHEN** `AGENT_ENV_SANDBOX_ENFORCEMENT=true` and `ToolAccessPolicy.evaluate()` returns `EXECUTE_SANDBOX` for tool `bash`
- **THEN** the tool executes inside the agent's DockerEnvironment container via `exec_shell()`
- **AND** `port.tool_execute_sandbox()` is NOT called for shell tools

#### Scenario: Non-sandbox tool routes to normal executor
- **WHEN** ToolWorker executes a tool call with decision `EXECUTE`
- **THEN** `port.tool_execute()` is called as before

#### Scenario: Sandbox does not bypass approval
- **WHEN** a tool has `sandbox_enabled=True` and `risk_level="critical"`
- **AND** no approval has been granted
- **THEN** the tool is NOT executed (REQUIRE_APPROVAL takes precedence)

## ADDED Requirements

### Requirement: SecurityAuditEvent emission from ToolAccessPolicy
ToolAccessPolicy.evaluate() SHALL emit a SecurityAuditEvent for each evaluation, capturing the tool name, access decision, matched rules, risk level, and policy version. Emission SHALL occur through an AuditSink interface to maintain engine layer zero-dependency constraint.

#### Scenario: REQUIRE_APPROVAL decision emits audit event
- **WHEN** `ToolAccessPolicy.evaluate()` returns `REQUIRE_APPROVAL`
- **THEN** a SecurityAuditEvent is emitted with decision="require_approval", matched rule, and risk level

#### Scenario: DENY decision emits audit event
- **WHEN** `ToolAccessPolicy.evaluate()` returns `DENY` due to dangerous pattern match
- **THEN** a SecurityAuditEvent is emitted with decision="deny", reason="dangerous_pattern_matched"
