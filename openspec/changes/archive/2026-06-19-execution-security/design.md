## Context

ToolModel stores `risk_level` (String, default "LOW"), `approval_required` (Boolean, default False), `sandbox_enabled` (Boolean, default False), and `sandbox_config` (JSON) fields. AgentModel stores `risk_level` and `guardrail_config` (JSON). A Docker SandboxExecutor and container pool are fully implemented (9.4c/9.4d). EnginePort exposes `tool_execute_sandbox()` as an optional method.

However, ToolWorker calls `port.tool_execute()` unconditionally — none of these fields influence execution. The platform has zero tool-level security enforcement.

A 10-platform research survey (Claude Code, Salesforce Agentforce, Google ADK, IBM watsonx, HermesAgent, OpenClaw, openJiuwen, Huawei AgentArts, Alibaba AgentScope, AutoGPT) revealed that no platform uses an explicit 4-level risk taxonomy, but the industry is converging on allow/deny/ask rule engines (Claude Code, HermesAgent RFC #21849) combined with sandbox isolation. HermesAgent's container-bypass-approval pattern was identified as CVE-2026-29607 (9.9 Critical) — we explicitly avoid it.

## Goals / Non-Goals

**Goals:**
- Enforce `approval_required`, `sandbox_enabled`, and `risk_level` fields that already exist on ToolModel
- Provide a rule engine for precise per-tool allow/deny/ask pattern matching
- Route sandbox-enabled tools to `tool_execute_sandbox()` instead of `tool_execute()`
- Support human-in-the-loop approval via a blocking callback with fail-closed timeout
- Persist approval decisions for scope-based caching (SESSION/PROJECT/GLOBAL)
- Maintain backward compatibility (no policy configured = EXECUTE for all tools)

**Non-Goals:**
- Approval API endpoints (deferred to feature 9.4e)
- Async approval mode with session resumption (OpenClaw pattern — future enhancement)
- Graph-level interrupt integration for approval (Command.interrupt re-enter semantics — future engine hardening)
- Multi-channel approval routing (Slack/Discord/Telegram — future enhancement)
- Per-operation granular toggles (feature 9.4a — 40+ operations)
- Trusted workspace auto-allow (feature 9.4b)
- Content moderation (feature 9.2a)

## Decisions

### D24: RiskLevel as StrEnum (LOW/MEDIUM/HIGH/CRITICAL)

Define `RiskLevel(StrEnum)` in `engine/tool_access.py`. Storage on ToolModel remains `String(20)` for backward compatibility — code uses the enum, DB stores the string value. No migration needed for existing data.

Each level maps to a default enforcement behavior when no explicit rules apply:
- LOW: auto-execute (read-only, idempotent tools)
- MEDIUM: auto-execute; sandbox if `sandbox_enabled` is set
- HIGH: require approval unless `sandbox_enabled` is set
- CRITICAL: always require approval regardless of sandbox

**Alternatives rejected:**
- Migrate column to native enum type — unnecessary migration risk for no functional gain
- Keep as free-form string — loses type safety and default semantics
- Use Salesforce MCP annotations (readOnly/destructive/idempotent/openWorld) — hints not enforcement; orthogonal to risk levels

### D25: Three-layer evaluation (rules → risk level → sandbox)

Evaluation order in `ToolAccessPolicy.evaluate()`:

```
Layer 1: Rule Engine (precise — Claude Code / HermesAgent pattern)
  → Workspace-level deny rules (ToolPolicyModel) — absolute, cannot be overridden
  → Agent-level allow/ask rules (guardrail_config) — per-agent customization
  → Pattern matching: tool_name(glob), e.g. "terminal(rm *)", "write_file(.env*)"

Layer 2: Risk Level Policy (default — our differentiator)
  → If no rule matched, use risk_level to determine default behavior
  → LOW → EXECUTE
  → MEDIUM → EXECUTE (or EXECUTE_SANDBOX if sandbox_enabled)
  → HIGH → REQUIRE_APPROVAL (or EXECUTE_SANDBOX if sandbox_enabled)
  → CRITICAL → REQUIRE_APPROVAL (always, regardless of sandbox)

Layer 3: Sandbox Routing (isolation — existing infrastructure)
  → If sandbox_enabled: route to port.tool_execute_sandbox()
  → If not: route to port.tool_execute()
  → Sandbox does NOT bypass approval (contra HermesAgent CVE-2026-29607)
```

**Alternatives rejected:**
- Risk-level-only (no rule engine) — too coarse, cannot express "allow git but deny rm"
- Rule-engine-only (no risk levels) — loses default semantics, 10-platform research shows this is the gap HermesAgent's RFC #21849 is trying to fill
- Sandbox bypasses approval (HermesAgent pattern) — CVE-2026-29607 (9.9 Critical), explicitly rejected

### D26: ToolAccessPolicy in engine layer (zero dependencies)

`ToolAccessPolicy` is a concrete class in `engine/tool_access.py` (consistent with `ToolGateEvaluator` in `engine/tool_gate.py`). It takes tool metadata + rules + context as parameters and returns an `AccessDecision`. Does not query the database — rule data is passed in by the caller (ToolWorker).

**Alternatives rejected:**
- ABC with pluggable implementations — over-engineering at this stage, one evaluation strategy suffices
- Service-layer class — would break engine's zero-dependency constraint
- PreToolHook implementation — GuardrailAction only has ALLOW/BLOCK/SANITIZE, no REQUIRE_APPROVAL outcome

### D27: ApprovalCallback blocking pattern (not Command.interrupt)

Approval is implemented as a blocking async callback within ToolWorker, NOT via `Command.interrupt`. The existing interrupt mechanism resumes to the NEXT node after the interrupted node, but the tool hasn't executed yet — this would produce a conversation without tool results.

```python
class ApprovalCallback(ABC):
    async def request_approval(
        self, tool_name: str, arguments: dict, risk_level: str, context: dict
    ) -> ApprovalDecision: ...

@dataclass
class ApprovalDecision:
    approved: bool
    reason: str = ""
    scope: ApprovalScope = ApprovalScope.ONCE
```

ToolWorker awaits `approval_callback.request_approval()` which blocks until a decision arrives or timeout expires. Timeout = deny (fail-closed), consistent with HermesAgent and OpenClaw.

**Alternatives rejected:**
- Command.interrupt — resume jumps to next node, tool never executes (see analysis above)
- Modify PregelRuntime to support re-enter on interrupt — too invasive, changes interrupt contract for all use cases
- Async mode (OpenClaw pattern) — returns immediately with approval_id, session continues — too complex for MVP, deferred to future enhancement

### D28: Rule storage — workspace-level ToolPolicyModel + agent-level guardrail_config

Two-layer rule storage with Claude Code-style precedence:

```
Layer 1: ToolPolicyModel (workspace-level, DB table)
  → Primarily for DENY rules (security baseline, set by workspace admin)
  → Cannot be overridden by agent-level rules
  → Fields: workspace_id, rule_action (DENY/ASK/ALLOW), tool_pattern, priority

Layer 2: AgentModel.guardrail_config (agent-level, JSON dict)
  → Per-agent customization
  → Can ADD allow/ask rules but cannot override workspace DENY
  → Format: {"tool_rules": {"allow": ["terminal(git:*)"], "ask": ["write_file(.env*)"]}}
  → Can also set "min_auto_approve_risk" (e.g., "MEDIUM" = tools above MEDIUM need approval)
```

Evaluation order: workspace DENY → agent DENY → agent ASK → agent ALLOW → risk_level fallback.

**Alternatives rejected:**
- Rules only in guardrail_config (no DB table) — no workspace-level baseline, cannot enforce admin-set deny rules
- Rules only in DB table (no agent config) — too rigid, agents can't customize
- Settings hierarchy like Claude Code (Managed > CLI > Local > Shared > User) — over-engineered for our single-tenant-per-workspace model

### D29: Fail-closed timeout (deny on timeout)

Default timeout: 60 seconds (configurable per agent via `guardrail_config.approval_timeout`). On timeout, `ApprovalDecision(approved=False, reason="Approval timeout")` is returned. For automation/cron scenarios (no interactive user), `guardrail_config.approval_mode = "deny"` short-circuits to immediate deny without waiting.

This matches HermesAgent (`approvals.timeout: 60`, deny on timeout) and OpenClaw (deny on timeout) patterns. No platform uses fail-open (allow on timeout).

### D30: ApprovalScope (ONCE/SESSION/PROJECT/GLOBAL)

```python
class ApprovalScope(StrEnum):
    ONCE = "once"        # Re-approve every invocation (default)
    SESSION = "session"  # Cache for current session (in-memory)
    PROJECT = "project"  # Persist to DB, valid across sessions in workspace
    GLOBAL = "global"    # Admin-level auto-approve (config-based)
```

ApprovalCallbackImpl checks scope cache before blocking:
- ONCE: always call `request_approval()`
- SESSION: check in-memory dict `{(session_id, tool_name): decision}`
- PROJECT: query ApprovalRecord table for active approval
- GLOBAL: check agent/workspace config for auto-approve rules

**Alternatives rejected:**
- Only ONCE and ALWAYS (Claude Code simplified) — loses granularity for team workflows
- Only ONCE (Google ADK invocation-based) — too much friction for repeated tool calls

## Risks / Trade-offs

**Risk: ToolWorker blocking on approval stalls the event loop**
Mitigation: `ApprovalCallback.request_approval()` is async; the event loop continues processing other tasks. Only the specific tool call is blocked.

**Risk: No crash recovery during approval wait**
Trade-off: Tool-level blocking (not graph-level interrupt) means no checkpoint during wait. If the process crashes, the pending approval is lost. Accepted for MVP — graph-level interrupt hardening is a future enhancement.

**Risk: Rule engine complexity**
Mitigation: Start simple — tool-name glob patterns only. Content matching (argument patterns like Claude Code's `Bash(git *)`) deferred to 9.4a.

**Trade-off: Workspace-level rules require DB query on every tool call**
Mitigation: ToolPolicyModel results cached per-session in ToolWorker. Rules are workspace-scoped and rarely change.

**Trade-off: ApprovalScope caching could approve stale tool calls**
Mitigation: PROJECT/GLOBAL approvals are tool-name scoped, not argument-scoped. Argument-scoped approval deferred to 9.4a.
