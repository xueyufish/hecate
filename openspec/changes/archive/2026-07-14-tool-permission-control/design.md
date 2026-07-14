## Context

Hecate has a well-developed tool security system spanning three components:

1. **ToolGateEvaluator** (`engine/tool_gate.py`) — evaluates `available_when` expressions per-tool to control visibility (hide from LLM context). Uses Python `eval()` with restricted namespace.
2. **ToolAccessPolicy** (`engine/tool_access.py`) — 5-layer execution-time evaluation: DangerousPattern → RuleEngine → WorkspaceBoundary → RiskLevel → SandboxRouting. Returns `AccessDecision` (EXECUTE / EXECUTE_SANDBOX / REQUIRE_APPROVAL / DENY).
3. **PreToolHook / PostToolHook** (`engine/guardrail.py`) — guardrail hooks for arbitrary pre/post-execution checks.

The problem: these are hardcoded and disconnected — you cannot add new layers, configure them per-agent, or compose them declaratively. Enterprise deployments need per-agent policies, plugin availability checks, and audit modes.

**Research basis** (14 platforms analyzed):
- OpenClaw: 8-layer composable pipeline (profile → provider → global → agent → provider → agent → sandbox → subagent), tool groups, MCP consent envelope
- AgentScope: 3-dimension model (Mode + Rules + Built-in Checks), 5 PermissionModes, `bypass_immune` flag, suggested rules auto-generation
- Salesforce Agentforce: `available when` per-action, re-evaluated every iteration, deterministic testing, platform RBAC (object/field/record)
- Amazon Bedrock AgentCore: Cedar policy-as-code, gateway boundary enforcement, Lambda interceptors, NLC→Cedar translation, automated reasoning validation
- Google Gemini Enterprise: Semantic Governance Policies using NLC, dry-run mode, per-agent identity
- IBM watsonx: `ToolPermission` enum (READ_ONLY/READ_WRITE), Security control center, OBO token exchange
- Huawei AgentArts: IAM-based (Action/Resource/Condition), production-grade sandbox, MCP gateway
- Dify: Plugin manifest permission declaration (coarse-grained)

## Goals / Non-Goals

**Goals:**
- Composable policy pipeline with pluggable layers (PolicyLayer ABC)
- 5 pipeline layers: PluginAvailability, Profile, Visibility, Security, Mode
- Per-agent policy configuration (mode + allow/deny lists, DB-backed)
- Declarative rule engine (glob patterns + arg conditions)
- PermissionMode (DEFAULT / RESTRICTED / AUDIT)
- Wrap existing ToolAccessPolicy and ToolGateEvaluator as pipeline layers (zero rewrite)
- REST API for policy CRUD
- Audit logging for every policy decision

**Non-Goals:**
- Cedar policy language (too heavy, new runtime)
- Natural Language Constraints (non-deterministic, unsuitable for security path)
- Per-channel restrictions (deferred to future enhancement)
- Frontend policy editor UI (API-first; UI in follow-up)
- Rewriting ToolAccessPolicy internal logic (wrap, don't rewrite)
- ACCEPT_EDITS / BYPASS modes (code-execution specific, not platform-level)
- Per-tenant connection pool isolation (handled by existing workspace_id)

## Decisions

### Decision 1: Wrap, don't rewrite (Option B)

**Choice**: Wrap existing `ToolAccessPolicy` and `ToolGateEvaluator` as pipeline layers. Do not rewrite their internal logic.

**Rationale**: The existing 5-layer ToolAccessPolicy is battle-tested and covers real security threats (dangerous patterns, workspace boundary, risk levels). Rewriting risks introducing security regressions. Wrapping is zero-risk migration and preserves all existing behavior. Agents without policy config get DEFAULT mode (backward compatible).

**Alternative rejected**: Full rewrite into pluggable layers (Option A). Higher risk, longer implementation, no incremental value.

### Decision 2: 5-layer pipeline architecture

**Choice**: Pipeline with 5 layers in fixed evaluation order.

```
Layer 0: PluginAvailabilityLayer  → DENY if plugin/MCP not enabled
Layer 1: ProfileLayer             → DENY/ALLOW based on per-agent rules
Layer 2: VisibilityLayer          → HIDE if available_when fails (LLM context only)
Layer 3: SecurityLayer            → EXECUTE/SANDBOX/APPROVAL/DENY (wraps ToolAccessPolicy)
Layer 4: ModeLayer               → Override based on PermissionMode
```

**Rationale**: 
- PluginAvailability first because it's the cheapest check (no expression eval, no rule matching)
- Profile before Security because per-agent rules should take precedence over generic risk-level fallback
- Visibility only affects LLM context (HIDE), not execution-time
- Security wraps existing logic — the most complex layer, unchanged
- Mode last because AUDIT mode needs to see the final decision from Security before overriding

**Alternative rejected**: OpenClaw's 8-layer pipeline — too complex for Hecate's use case, most layers (provider-level, subagent-level) don't apply.

### Decision 3: 3 PermissionModes (not 5)

**Choice**: DEFAULT, RESTRICTED, AUDIT.

| Mode | Behavior | Reference |
|------|----------|-----------|
| DEFAULT | Normal pipeline evaluation | AgentScope DEFAULT |
| RESTRICTED | Only allowlisted tools pass (ProfileLayer must ALLOW) | AgentScope EXPLORE |
| AUDIT | All tools allowed, but every decision logged | Google SGP dry-run |

**Rationale**: AgentScope's 5 modes (DEFAULT/ACCEPT_EDITS/EXPLORE/BYPASS/DONT_ASK) are designed for code-execution scenarios. Hecate is a platform layer — ACCEPT_EDITS and BYPASS are agent-framework concerns, not platform concerns. DONT_ASK is covered by not configuring approval callbacks.

**AUDIT mode value**: Enterprises can deploy in AUDIT mode for a week, review which tools would be denied, then switch to DEFAULT. This is exactly Google's dry-run pattern.

### Decision 4: Declarative rules (glob + arg_conditions), not policy DSL

**Choice**: `ToolPolicyRuleModel` with glob patterns for tool names + optional arg_conditions dict (glob patterns per argument key). Same semantics as existing `ToolRule` in `tool_access.py`.

**Rationale**: 
- Cedar is too heavy (new language + runtime)
- NLC is non-deterministic
- Python eval() is only for `available_when` (visibility), not for policy rules
- Glob patterns are what OpenClaw, AgentScope, and our existing ToolRule already use — proven, simple, auditable

### Decision 5: Two interception points preserved

**Choice**: Pipeline runs at two points:
1. **Visibility filtering** (in LLMWorker before LLM call): runs PluginAvailability + Profile + Visibility layers. Tools that get HIDE decision are removed from the tool list sent to LLM.
2. **Execution-time evaluation** (in ToolWorker before tool call): runs all 5 layers. Gets final ALLOW/DENY/REQUIRE_APPROVAL/EXECUTE_SANDBOX decision.

**Rationale**: Salesforce Agentforce proved that `available when` (visibility) and execution-time access control are two distinct concerns. Hecate already has both interception points — we preserve them.

## Risks / Trade-offs

- **[Performance overhead]** — Pipeline evaluates multiple layers per tool call. Mitigation: layers are pure Python with no I/O (except plugin availability check which can be cached). Existing ToolAccessPolicy already evaluates 5 layers; adding 2 more is negligible.

- **[AUDIT mode false sense of security]** — AUDIT mode allows all tools but logs decisions. Operators might forget to switch to DEFAULT. Mitigation: AUDIT mode logs WARNING on every DENY that was overridden to ALLOW. Dashboard can show "AUDIT mode active" banner.

- **[Plugin availability check latency]** — Checking plugin enabled status per tool call could add latency. Mitigation: plugin status is cached in-memory by PluginService; check is a dict lookup, not DB query.

- **[Backward compatibility]** — Existing agents without policy config must work identically. Mitigation: DEFAULT mode is the zero-config default; SecurityLayer wraps existing ToolAccessPolicy unchanged.
