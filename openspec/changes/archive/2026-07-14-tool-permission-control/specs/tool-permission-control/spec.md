## ADDED Requirements — 新增需求

### Requirement: Composable policy pipeline — 需求：可组合的策略管线
The system SHALL provide a `ToolPolicyPipeline` that evaluates tool access through ordered, pluggable `PolicyLayer` instances. Each layer receives `(tool, context)` and returns a `PolicyDecision` (ALLOW / DENY / HIDE / REQUIRE_APPROVAL / EXECUTE_SANDBOX). DENY short-circuits the pipeline. HIDE removes the tool from LLM visibility only.

系统应提供 `ToolPolicyPipeline`，通过有序、可插拔的 `PolicyLayer` 实例评估工具访问。每个层接收 `(tool, context)` 并返回 `PolicyDecision`（ALLOW / DENY / HIDE / REQUIRE_APPROVAL / EXECUTE_SANDBOX）。DENY 短路管线。HIDE 仅从 LLM 可见性中移除工具。

#### Scenario: Layer deny short-circuits — 场景：层拒绝短路
- **WHEN** any layer returns DENY
- **THEN** the pipeline stops evaluation and returns DENY immediately

- **当**任何层返回 DENY
- **则**管线停止评估并立即返回 DENY

#### Scenario: Layer hide short-circuits visibility — 场景：层隐藏短路可见性
- **WHEN** the VisibilityLayer returns HIDE during visibility filtering
- **THEN** the tool is removed from the LLM's tool list, but execution-time evaluation proceeds normally

- **当** VisibilityLayer 在可见性过滤期间返回 HIDE
- **则**工具从 LLM 的工具列表中移除，但执行时评估正常进行

#### Scenario: All layers pass through — 场景：所有层通过
- **WHEN** all layers return ALLOW or PASSTHROUGH
- **THEN** the pipeline returns ALLOW

- **当**所有层返回 ALLOW 或 PASSTHROUGH
- **则**管线返回 ALLOW

### Requirement: PluginAvailabilityLayer — 需求：PluginAvailabilityLayer
The system SHALL check whether a tool's source plugin or MCP server is enabled before allowing access. If the plugin is disabled or the MCP server is unregistered, the layer returns DENY.

系统应在允许访问前检查工具的源插件或 MCP 服务器是否已启用。如果插件已禁用或 MCP 服务器未注册，该层返回 DENY。

#### Scenario: Plugin enabled — 场景：插件已启用
- **WHEN** the tool's source plugin is enabled (or is a built-in tool)
- **THEN** the layer returns ALLOW

- **当**工具的源插件已启用（或者是内置工具）
- **则**该层返回 ALLOW

#### Scenario: Plugin disabled — 场景：插件已禁用
- **WHEN** the tool's source plugin is disabled
- **THEN** the layer returns DENY with reason "plugin not enabled"

- **当**工具的源插件已禁用
- **则**该层返回 DENY，原因为"插件未启用"

#### Scenario: MCP server unregistered — 场景：MCP 服务器未注册
- **WHEN** the tool's source is an MCP server that is not currently registered
- **THEN** the layer returns DENY with reason "MCP server not available"

- **当**工具的来源是当前未注册的 MCP 服务器
- **则**该层返回 DENY，原因为"MCP 服务器不可用"

### Requirement: ProfileLayer with declarative rules — 需求：带声明式规则的 ProfileLayer
The system SHALL evaluate per-agent and per-workspace declarative policy rules using glob pattern matching on tool names and optional argument conditions. Rules are ordered by priority; higher priority rules are evaluated first. DENY rules take precedence over ALLOW rules at the same tier.

系统应使用工具名称上的 glob 模式匹配和可选参数条件评估每个代理和每个工作空间的声明式策略规则。规则按优先级排序；高优先级规则先评估。在同一层级，DENY 规则优先于 ALLOW 规则。

#### Scenario: Agent-specific allow rule matches — 场景：代理特定的允许规则匹配
- **WHEN** an agent has an allow rule with pattern `web_search` and the tool name matches
- **THEN** the layer returns ALLOW

- **当**代理具有模式为 `web_search` 的允许规则且工具名称匹配
- **则**该层返回 ALLOW

#### Scenario: Agent-specific deny rule matches — 场景：代理特定的拒绝规则匹配
- **WHEN** an agent has a deny rule with pattern `bash` and the tool name matches
- **THEN** the layer returns DENY with reason "denied by agent policy rule"

- **当**代理具有模式为 `bash` 的拒绝规则且工具名称匹配
- **则**该层返回 DENY，原因为"被代理策略规则拒绝"

#### Scenario: Arg condition filtering — 场景：参数条件过滤
- **WHEN** a rule has `arg_conditions: {"path": "/workspace/*"}` and the tool call argument `path` does not match
- **THEN** the rule is skipped (does not match)

- **当**规则具有 `arg_conditions: {"path": "/workspace/*"}` 且工具调用参数 `path` 不匹配
- **则**规则被跳过（不匹配）

#### Scenario: No rules configured — 场景：未配置规则
- **WHEN** no policy rules are configured for the agent or workspace
- **THEN** the layer returns ALLOW (passthrough)

- **当**代理或工作空间未配置策略规则
- **则**该层返回 ALLOW（直通）

### Requirement: VisibilityLayer replaces ToolGateEvaluator — 需求：VisibilityLayer 替换 ToolGateEvaluator
The system SHALL evaluate `available_when` expressions through the pipeline's VisibilityLayer, replacing the standalone `ToolGateEvaluator`. Expression semantics (Python eval with restricted namespace, fail-closed) are preserved.

系统应通过管线的 VisibilityLayer 评估 `available_when` 表达式，替换独立的 `ToolGateEvaluator`。表达式语义（带受限命名空间的 Python eval，故障关闭）保持不变。

#### Scenario: Available when expression passes — 场景：可用条件表达式通过
- **WHEN** the `available_when` expression evaluates to truthy given the runtime context
- **THEN** the layer returns ALLOW during visibility filtering

- **当** `available_when` 表达式在给定运行时上下文下评估为真值
- **则**该层在可见性过滤期间返回 ALLOW

#### Scenario: Available when expression fails — 场景：可用条件表达式失败
- **WHEN** the `available_when` expression evaluates to falsy or raises an exception
- **THEN** the layer returns HIDE during visibility filtering

- **当** `available_when` 表达式评估为假值或抛出异常
- **则**该层在可见性过滤期间返回 HIDE

#### Scenario: No available_when expression — 场景：无可用的条件表达式
- **WHEN** the tool has no `available_when` field (None)
- **THEN** the layer returns ALLOW (always visible)

- **当**工具没有 `available_when` 字段（None）
- **则**该层返回 ALLOW（始终可见）

### Requirement: SecurityLayer wraps existing ToolAccessPolicy — 需求：SecurityLayer 包装现有的 ToolAccessPolicy
The system SHALL wrap the existing `ToolAccessPolicy` (5-layer: DangerousPattern, RuleEngine, WorkspaceBoundary, RiskLevel, SandboxRouting) as a pipeline layer. Internal evaluation logic is unchanged.

系统应将现有的 `ToolAccessPolicy`（5 层：DangerousPattern、RuleEngine、WorkspaceBoundary、RiskLevel、SandboxRouting）包装为管线层。内部评估逻辑不变。

#### Scenario: Dangerous pattern detected — 场景：检测到危险模式
- **WHEN** the tool call matches a built-in dangerous pattern
- **THEN** the layer returns DENY (bypass-immune)

- **当**工具调用匹配内置危险模式
- **则**该层返回 DENY（免于绕过）

#### Scenario: High risk tool without sandbox — 场景：无沙箱的高风险工具
- **WHEN** a tool has `risk_level=HIGH` and `sandbox_enabled=False`
- **THEN** the layer returns REQUIRE_APPROVAL

- **当**工具具有 `risk_level=HIGH` 且 `sandbox_enabled=False`
- **则**该层返回 REQUIRE_APPROVAL

#### Scenario: Tool with sandbox enabled — 场景：启用沙箱的工具
- **WHEN** a tool has `sandbox_enabled=True` and passes all security checks
- **THEN** the layer returns EXECUTE_SANDBOX

- **当**工具具有 `sandbox_enabled=True` 且通过所有安全检查
- **则**该层返回 EXECUTE_SANDBOX

### Requirement: ModeLayer with PermissionMode — 需求：带 PermissionMode 的 ModeLayer
The system SHALL support three PermissionModes that control the final pipeline decision globally per agent.

系统应支持三种 PermissionMode，全局控制每个代理的最终管线决策。

#### Scenario: DEFAULT mode — 场景：DEFAULT 模式
- **WHEN** the agent's PermissionMode is DEFAULT
- **THEN** the pipeline uses the SecurityLayer's decision unchanged

- **当**代理的 PermissionMode 为 DEFAULT
- **则**管线使用 SecurityLayer 的决策不变

#### Scenario: RESTRICTED mode with non-allowlisted tool — 场景：RESTRICTED 模式且工具不在白名单中
- **WHEN** the agent's PermissionMode is RESTRICTED and the tool is not in the agent's allowlist
- **THEN** the pipeline returns DENY with reason "tool not in restricted allowlist"

- **当**代理的 PermissionMode 为 RESTRICTED 且工具不在代理的白名单中
- **则**管线返回 DENY，原因为"工具不在限制性白名单中"

#### Scenario: AUDIT mode overrides deny — 场景：AUDIT 模式覆盖拒绝
- **WHEN** the agent's PermissionMode is AUDIT and a prior layer returned DENY
- **THEN** the pipeline overrides to ALLOW but logs the original DENY decision with WARNING level

- **当**代理的 PermissionMode 为 AUDIT 且先前的层返回了 DENY
- **则**管线覆盖为 ALLOW，但以 WARNING 级别记录原始的 DENY 决策

#### Scenario: AUDIT mode preserves require_approval — 场景：AUDIT 模式保留 REQUIRE_APPROVAL
- **WHEN** the agent's PermissionMode is AUDIT and a prior layer returned REQUIRE_APPROVAL
- **THEN** the pipeline preserves REQUIRE_APPROVAL (does not auto-approve dangerous operations)

- **当**代理的 PermissionMode 为 AUDIT 且先前的层返回了 REQUIRE_APPROVAL
- **则**管线保留 REQUIRE_APPROVAL（不自动批准危险操作）

### Requirement: Per-agent policy configuration — 需求：每个代理的策略配置
The system SHALL store per-agent policy configuration in the database, including PermissionMode and tool allowlist/denylist. Agents without a policy configuration default to DEFAULT mode.

系统应在数据库中存储每个代理的策略配置，包括 PermissionMode 和工具允许列表/拒绝列表。没有策略配置的代理默认为 DEFAULT 模式。

#### Scenario: Agent with restricted policy — 场景：带限制策略的代理
- **WHEN** an agent has `AgentPolicyConfig` with mode=RESTRICTED and allowlist=["web_search"]
- **THEN** only `web_search` tool calls are allowed; all others are denied

- **当**代理具有 mode=RESTRICTED 且 allowlist=["web_search"] 的 `AgentPolicyConfig`
- **则**仅允许 `web_search` 工具调用；所有其他调用被拒绝

#### Scenario: Agent without policy config — 场景：无策略配置的代理
- **WHEN** an agent has no `AgentPolicyConfig` (None)
- **THEN** the agent uses DEFAULT mode (backward compatible)

- **当**代理没有 `AgentPolicyConfig`（None）
- **则**代理使用 DEFAULT 模式（向后兼容）

### Requirement: Policy rule management — 需求：策略规则管理
The system SHALL store declarative policy rules in the database with glob patterns for tool names, optional argument conditions, action (allow/deny/ask), and priority. Rules are scoped to workspace or agent level.

系统应在数据库中存储声明式策略规则，包含工具名称的 glob 模式、可选参数条件、操作（allow/deny/ask）和优先级。规则限定到工作空间或代理级别。

#### Scenario: Create workspace-level rule — 场景：创建工作空间级规则
- **WHEN** a client creates a rule with `agent_id=None`
- **THEN** the rule applies to all agents in the workspace

- **当**客户端创建 `agent_id=None` 的规则
- **则**规则应用于工作空间中的所有代理

#### Scenario: Create agent-level rule — 场景：创建代理级规则
- **WHEN** a client creates a rule with a specific `agent_id`
- **THEN** the rule applies only to that agent, and takes precedence over workspace-level rules

- **当**客户端创建具有特定 `agent_id` 的规则
- **则**规则仅应用于该代理，并且优先于工作空间级规则

#### Scenario: Rule priority ordering — 场景：规则优先级排序
- **WHEN** two rules match the same tool with different priorities
- **THEN** the higher-priority rule's action wins

- **当**两条规则以不同优先级匹配同一工具
- **则**高优先级规则的操作获胜

### Requirement: REST API for policy management — 需求：策略管理的 REST API
The system SHALL expose REST API endpoints for managing tool policy rules and agent policy configurations.

系统应公开用于管理工具策略规则和代理策略配置的 REST API 端点。

#### Scenario: List policy rules — 场景：列出策略规则
- **WHEN** a client requests `GET /api/tool-policies/rules`
- **THEN** the system returns all policy rules for the workspace, optionally filtered by agent_id

- **当**客户端请求 `GET /api/tool-policies/rules`
- **则**系统返回工作空间的所有策略规则，可按 agent_id 过滤

#### Scenario: Create policy rule — 场景：创建策略规则
- **WHEN** a client requests `POST /api/tool-policies/rules` with rule data
- **THEN** the system creates the rule and returns 201

- **当**客户端使用规则数据请求 `POST /api/tool-policies/rules`
- **则**系统创建规则并返回 201

#### Scenario: Update agent policy config — 场景：更新代理策略配置
- **WHEN** a client requests `PUT /api/tool-policies/agents/{agent_id}/config` with mode and allowlist
- **THEN** the system updates the agent's policy configuration

- **当**客户端使用模式和允许列表请求 `PUT /api/tool-policies/agents/{agent_id}/config`
- **则**系统更新代理的策略配置

#### Scenario: Delete policy rule — 场景：删除策略规则
- **WHEN** a client requests `DELETE /api/tool-policies/rules/{id}`
- **THEN** the system deletes the rule and returns 204

- **当**客户端请求 `DELETE /api/tool-policies/rules/{id}`
- **则**系统删除规则并返回 204

### Requirement: Audit logging — 需求：审计日志
The system SHALL log every policy decision at DEBUG level with tool name, agent ID, each layer's decision, and the final pipeline decision. In AUDIT mode, DENY-overridden-to-ALLOW decisions are logged at WARNING level.

系统应以 DEBUG 级别记录每个策略决策，包含工具名称、代理 ID、每个层的决策和最终管线决策。在 AUDIT 模式下，DENY 被覆盖为 ALLOW 的决策以 WARNING 级别记录。

#### Scenario: Normal mode logging — 场景：正常模式日志
- **WHEN** the pipeline evaluates a tool call in DEFAULT mode
- **THEN** the system logs the decision at DEBUG level with per-layer breakdown

- **当**管线在 DEFAULT 模式下评估工具调用
- **则**系统以 DEBUG 级别记录决策，包含逐层分解

#### Scenario: AUDIT mode override warning — 场景：AUDIT 模式覆盖警告
- **WHEN** AUDIT mode overrides a DENY to ALLOW
- **THEN** the system logs a WARNING with the original DENY reason and the tool name

- **当** AUDIT 模式将 DENY 覆盖为 ALLOW
- **则**系统以 WARNING 级别记录原始的 DENY 原因和工具名称
