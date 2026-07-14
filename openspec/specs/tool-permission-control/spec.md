## ADDED Requirements

### Requirement: Composable policy pipeline
The system SHALL provide a `ToolPolicyPipeline` that evaluates tool access through ordered, pluggable `PolicyLayer` instances. Each layer receives `(tool, context)` and returns a `PolicyDecision` (ALLOW / DENY / HIDE / REQUIRE_APPROVAL / EXECUTE_SANDBOX). DENY short-circuits the pipeline. HIDE removes the tool from LLM visibility only.

#### Scenario: Layer deny short-circuits
- **WHEN** any layer returns DENY
- **THEN** the pipeline stops evaluation and returns DENY immediately

#### Scenario: Layer hide short-circuits visibility
- **WHEN** the VisibilityLayer returns HIDE during visibility filtering
- **THEN** the tool is removed from the LLM's tool list, but execution-time evaluation proceeds normally

#### Scenario: All layers pass through
- **WHEN** all layers return ALLOW or PASSTHROUGH
- **THEN** the pipeline returns ALLOW

### Requirement: PluginAvailabilityLayer
The system SHALL check whether a tool's source plugin or MCP server is enabled before allowing access. If the plugin is disabled or the MCP server is unregistered, the layer returns DENY.

#### Scenario: Plugin enabled
- **WHEN** the tool's source plugin is enabled (or is a built-in tool)
- **THEN** the layer returns ALLOW

#### Scenario: Plugin disabled
- **WHEN** the tool's source plugin is disabled
- **THEN** the layer returns DENY with reason "plugin not enabled"

#### Scenario: MCP server unregistered
- **WHEN** the tool's source is an MCP server that is not currently registered
- **THEN** the layer returns DENY with reason "MCP server not available"

### Requirement: ProfileLayer with declarative rules
The system SHALL evaluate per-agent and per-workspace declarative policy rules using glob pattern matching on tool names and optional argument conditions. Rules are ordered by priority; higher priority rules are evaluated first. DENY rules take precedence over ALLOW rules at the same tier.

#### Scenario: Agent-specific allow rule matches
- **WHEN** an agent has an allow rule with pattern `web_search` and the tool name matches
- **THEN** the layer returns ALLOW

#### Scenario: Agent-specific deny rule matches
- **WHEN** an agent has a deny rule with pattern `bash` and the tool name matches
- **THEN** the layer returns DENY with reason "denied by agent policy rule"

#### Scenario: Arg condition filtering
- **WHEN** a rule has `arg_conditions: {"path": "/workspace/*"}` and the tool call argument `path` does not match
- **THEN** the rule is skipped (does not match)

#### Scenario: No rules configured
- **WHEN** no policy rules are configured for the agent or workspace
- **THEN** the layer returns ALLOW (passthrough)

### Requirement: VisibilityLayer replaces ToolGateEvaluator
The system SHALL evaluate `available_when` expressions through the pipeline's VisibilityLayer, replacing the standalone `ToolGateEvaluator`. Expression semantics (Python eval with restricted namespace, fail-closed) are preserved.

#### Scenario: Available when expression passes
- **WHEN** the `available_when` expression evaluates to truthy given the runtime context
- **THEN** the layer returns ALLOW during visibility filtering

#### Scenario: Available when expression fails
- **WHEN** the `available_when` expression evaluates to falsy or raises an exception
- **THEN** the layer returns HIDE during visibility filtering

#### Scenario: No available_when expression
- **WHEN** the tool has no `available_when` field (None)
- **THEN** the layer returns ALLOW (always visible)

### Requirement: SecurityLayer wraps existing ToolAccessPolicy
The system SHALL wrap the existing `ToolAccessPolicy` (5-layer: DangerousPattern, RuleEngine, WorkspaceBoundary, RiskLevel, SandboxRouting) as a pipeline layer. Internal evaluation logic is unchanged.

#### Scenario: Dangerous pattern detected
- **WHEN** the tool call matches a built-in dangerous pattern
- **THEN** the layer returns DENY (bypass-immune)

#### Scenario: High risk tool without sandbox
- **WHEN** a tool has `risk_level=HIGH` and `sandbox_enabled=False`
- **THEN** the layer returns REQUIRE_APPROVAL

#### Scenario: Tool with sandbox enabled
- **WHEN** a tool has `sandbox_enabled=True` and passes all security checks
- **THEN** the layer returns EXECUTE_SANDBOX

### Requirement: ModeLayer with PermissionMode
The system SHALL support three PermissionModes that control the final pipeline decision globally per agent.

#### Scenario: DEFAULT mode
- **WHEN** the agent's PermissionMode is DEFAULT
- **THEN** the pipeline uses the SecurityLayer's decision unchanged

#### Scenario: RESTRICTED mode with non-allowlisted tool
- **WHEN** the agent's PermissionMode is RESTRICTED and the tool is not in the agent's allowlist
- **THEN** the pipeline returns DENY with reason "tool not in restricted allowlist"

#### Scenario: AUDIT mode overrides deny
- **WHEN** the agent's PermissionMode is AUDIT and a prior layer returned DENY
- **THEN** the pipeline overrides to ALLOW but logs the original DENY decision with WARNING level

#### Scenario: AUDIT mode preserves require_approval
- **WHEN** the agent's PermissionMode is AUDIT and a prior layer returned REQUIRE_APPROVAL
- **THEN** the pipeline preserves REQUIRE_APPROVAL (does not auto-approve dangerous operations)

### Requirement: Per-agent policy configuration
The system SHALL store per-agent policy configuration in the database, including PermissionMode and tool allowlist/denylist. Agents without a policy configuration default to DEFAULT mode.

#### Scenario: Agent with restricted policy
- **WHEN** an agent has `AgentPolicyConfig` with mode=RESTRICTED and allowlist=["web_search"]
- **THEN** only `web_search` tool calls are allowed; all others are denied

#### Scenario: Agent without policy config
- **WHEN** an agent has no `AgentPolicyConfig` (None)
- **THEN** the agent uses DEFAULT mode (backward compatible)

### Requirement: Policy rule management
The system SHALL store declarative policy rules in the database with glob patterns for tool names, optional argument conditions, action (allow/deny/ask), and priority. Rules are scoped to workspace or agent level.

#### Scenario: Create workspace-level rule
- **WHEN** a client creates a rule with `agent_id=None`
- **THEN** the rule applies to all agents in the workspace

#### Scenario: Create agent-level rule
- **WHEN** a client creates a rule with a specific `agent_id`
- **THEN** the rule applies only to that agent, and takes precedence over workspace-level rules

#### Scenario: Rule priority ordering
- **WHEN** two rules match the same tool with different priorities
- **THEN** the higher-priority rule's action wins

### Requirement: REST API for policy management
The system SHALL expose REST API endpoints for managing tool policy rules and agent policy configurations.

#### Scenario: List policy rules
- **WHEN** a client requests `GET /api/tool-policies/rules`
- **THEN** the system returns all policy rules for the workspace, optionally filtered by agent_id

#### Scenario: Create policy rule
- **WHEN** a client requests `POST /api/tool-policies/rules` with rule data
- **THEN** the system creates the rule and returns 201

#### Scenario: Update agent policy config
- **WHEN** a client requests `PUT /api/tool-policies/agents/{agent_id}/config` with mode and allowlist
- **THEN** the system updates the agent's policy configuration

#### Scenario: Delete policy rule
- **WHEN** a client requests `DELETE /api/tool-policies/rules/{id}`
- **THEN** the system deletes the rule and returns 204

### Requirement: Audit logging
The system SHALL log every policy decision at DEBUG level with tool name, agent ID, each layer's decision, and the final pipeline decision. In AUDIT mode, DENY-overridden-to-ALLOW decisions are logged at WARNING level.

#### Scenario: Normal mode logging
- **WHEN** the pipeline evaluates a tool call in DEFAULT mode
- **THEN** the system logs the decision at DEBUG level with per-layer breakdown

#### Scenario: AUDIT mode override warning
- **WHEN** AUDIT mode overrides a DENY to ALLOW
- **THEN** the system logs a WARNING with the original DENY reason and the tool name
