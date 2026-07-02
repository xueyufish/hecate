## Why

ToolModel already stores `risk_level`, `approval_required`, `sandbox_enabled`, and `sandbox_config` fields, but ToolWorker ignores them entirely — every tool call goes through `tool_execute()` unconditionally. This means the platform has zero enforcement of tool-level security policies: no risk-based gating, no sandbox routing, no human approval workflow. Feature 9.4 (Execution Security) closes this gap, completing the "Full security stack" milestone item and delivering a capability that no surveyed competitor (Claude Code, Salesforce, Google ADK, IBM watsonx, HermesAgent, OpenClaw, openJiuwen, Huawei AgentArts) offers as an integrated three-layer system.

## What Changes

- **RiskLevel enum** — Formalize the existing free-form `risk_level` string into a `StrEnum` (LOW/MEDIUM/HIGH/CRITICAL) with defined default enforcement semantics per level
- **ToolAccessPolicy** — New engine-layer class that evaluates tool metadata (risk_level, approval_required, sandbox_enabled) plus context against configurable rules, producing an `AccessDecision` (EXECUTE / EXECUTE_SANDBOX / REQUIRE_APPROVAL / DENY)
- **Rule engine** — Allow/deny/ask rules with tool-name pattern matching (Claude Code style), stored at two levels: workspace-level `ToolPolicyModel` (deny baseline) and agent-level `guardrail_config` (allow/ask overrides)
- **Sandbox routing** — Wire ToolWorker to route `sandbox_enabled` tools to `port.tool_execute_sandbox()` instead of `port.tool_execute()`
- **ApprovalCallback ABC** — New engine-layer abstract interface for blocking approval requests; ToolWorker awaits approval before executing tools that require it; timeout = deny (fail-closed)
- **ApprovalScope enum** — ONCE / SESSION / PROJECT / GLOBAL, controlling how long an approval decision remains valid
- **ApprovalRecord model** — New ORM model persisting approval decisions for scope caching (SESSION/PROJECT/GLOBAL)
- **ToolWorker integration** — Inject ToolAccessPolicy + ApprovalCallback into ToolWorker; enforce policy before every `tool_execute` call

## Capabilities

### New Capabilities
- `execution-security`: Three-layer tool execution security — rule engine (precise allow/deny/ask pattern matching) + risk-level policy (default enforcement per LOW/MEDIUM/HIGH/CRITICAL) + sandbox routing (existing DockerSandboxExecutor integration). Includes approval workflow via blocking ApprovalCallback with fail-closed timeout.

### Modified Capabilities
_(none — ToolModel already stores risk_level, approval_required, sandbox_enabled, sandbox_config fields; guardrail_config is already a flexible JSON dict. This change adds enforcement logic and new models without altering existing spec-level requirements.)_

## Impact

- **Engine layer** (`engine/tool_access.py`): New — ToolAccessPolicy, AccessDecision, RiskLevel, ApprovalScope, ApprovalCallback, ApprovalDecision, ToolRule, RuleAction
- **Engine layer** (`engine/workers/tool_worker.py`): Modified — policy evaluation before tool_execute, sandbox routing, approval callback integration
- **Models layer** (`models/approval.py`): New — ApprovalRecordModel + Pydantic schemas
- **Models layer** (`models/tool_policy.py`): New — ToolPolicyModel for workspace-level deny rules
- **Services layer** (`services/approval.py`): New — ApprovalCallbackImpl (DB persistence, notification, async wait, timeout)
- **Migration**: New — approval_records table, tool_policies table
- **No API layer changes** in this change — approval API endpoints deferred to feature 9.4e
- **No breaking changes** — existing fields are enforced, not restructured; default behavior (no policy configured) = EXECUTE for all tools (backward compatible)
