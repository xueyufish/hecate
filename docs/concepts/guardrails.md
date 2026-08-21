# Guardrails and Hooks

Every agent runtime has a trust problem: the LLM can produce harmful content, leak sensitive data, or call a dangerous tool. Most frameworks handle this by wrapping the LLM client or intercepting HTTP requests — solutions that are easy to bypass and hard to audit.

Hecate takes a different approach. The execution engine defines **four hook types** at the only four points where trust boundaries are crossed: before and after every LLM call, and before and after every tool execution. Because these hooks live in the engine — not in a wrapper — they cannot be bypassed by a misconfigured agent or a clever prompt. Every execution path passes through them.

> **Chain semantics** — since [guardrail-upgrade-trio](../changes/guardrail-upgrade-trio/) (T1), each of the four positions is an **ordered middleware chain** rather than a single hook slot. Multiple hooks (PII masker, injection detection, DLP, content moderation, HITL clarification, etc.) compose via `next()` delegation; BLOCK short-circuits the chain with the originating stage's identity, SANITIZE flows modified data downstream. The single-hook construction parameters remain as backward-compatible adapters. See [Middleware chain](#middleware-chain-and-tool-policy) below.

> **Outbound detection** — the policy-driven PII/secrets/content scanning engine that powers PII masking and tool-result sanitization today is the [DLP engine](dlp.md). It plugs into these same hooks (plus the MCP egress filter) with configurable per-entity actions (`ALLOW`/`MASK`/`BLOCK`/`AUDIT`). Read [DLP](dlp.md) for the detection-and-policy layer; this page covers the hook interception points themselves.

---

## The four hook points

The engine's Pregel superstep loop dispatches work to workers. Workers do two kinds of external work: calling an LLM, and executing a tool. Each of those has a "before" and "after" moment, giving four hooks:

| Hook | Fires | Typical use |
|------|-------|-------------|
| **PreLLMHook** | Before messages are sent to the LLM | PII masking on the prompt, prompt-injection detection, token-budget enforcement |
| **PostLLMHook** | After the LLM returns a response | Content filtering, response redaction, toxic-output blocking |
| **PreToolHook** | Before a tool is invoked | Permission check, risk-level gating, argument validation |
| **PostToolHook** | After a tool returns | Result auditing, sensitive-output redaction, side-effect logging |

```
           ┌─ PreLLMHook ─┐   ┌─ PostLLMHook ─┐
LLM call → │              │ → │                │ → response used
           └──────────────┘   └────────────────┘

           ┌─ PreToolHook ┐   ┌─ PostToolHook ─┐
Tool call →│              │ → │                │ → result used
           └──────────────┘   └────────────────┘
```

Each hook receives the full execution context — the agent, the session, the messages or arguments, and the request metadata. A hook can inspect the data, modify it, or **veto** the operation by raising an exception that aborts the superstep.

---

## Why engine-level, not wrapper-level

The placement matters. Consider the alternatives:

- **LLM-client wrappers** only intercept calls that go through the wrapped client. A workflow node that calls the LLM through a different path (a sub-agent, a code-execution node that builds its own request) bypasses the wrapper entirely.
- **HTTP proxies** sit outside the application and cannot see the agent's intent, the session context, or which tool is being called. They can redact strings but cannot make risk-aware decisions.
- **Prompt-level instructions** ("do not leak PII") are requests, not guarantees. They are ignored by design and trivially defeated.

Hecate's hooks sit inside the engine's execution loop. Every LLM invocation and every tool execution — regardless of which node triggered it, whether it came from a sub-agent or a code-execution sandbox — passes through the same four hooks. There is no alternate code path.

---

## What hooks can do

A hook is a Python class implementing a single method. The four abstract base classes are `PreLLMHook`, `PostLLMHook`, `PreToolHook`, and `PostToolHook`. Default no-op implementations (`NoOpPreLLMHook`, etc.) pass everything through unchanged.

A hook implementation can:

- **Inspect** the request or response (messages, tool name, arguments, result).
- **Transform** the data before it continues (mask a credit card number, redact a secret).
- **Block** the operation by raising an exception, which the engine converts into a failed superstep.
- **Log** the event to the audit trail with the full context.
- **Trigger** a human-approval flow by calling `interrupt()` inside the hook.

This is how Hecate builds higher-level security features from the same four primitives:

| Feature | How it uses hooks |
|---------|-------------------|
| **PII masking** | `PreLLMHook` scans the prompt and replaces sensitive patterns before the LLM sees them; `PostLLMHook` re-applies masking to the response |
| **Audit logging** | All four hooks write structured events to the audit trail with agent, session, and user context |
| **Content filtering (LLM Guard)** | `PreLLMHook` rejects prompt-injection attempts; `PostLLMHook` blocks toxic or policy-violating outputs |
| **Tool permission enforcement** | `PreToolHook` checks the tool's risk level against the agent's permissions and the user's role |
| **Human-in-the-loop approval** | `PreToolHook` calls `interrupt()` for `HIGH` or `CRITICAL` risk tools, pausing until a human approves via `Command` |

---

## Built-in hook implementations

Hecate ships concrete hook implementations that cover the most common security needs. The four hooks are bundled together in a `SecurityHookSet` namedtuple constructed via `create_security_hooks(guardrail_config)`, which assembles the set from per-agent configuration:

| Hook position | Built-in implementation | What it does |
|---------------|------------------------|-------------|
| `PreLLMHook` | `InputSecurityHook` | PII detection, prompt-injection screening, input sanitization |
| `PostLLMHook` | `OutputSecurityHook` | Output content filtering, response sanitization |
| `PreToolHook` | (configured per-tool) | Permission check against agent permissions and tool risk level |
| `PostToolHook` | `ToolResultSecurityHook` | Tool-result sanitization, sensitive-output redaction |

When no per-agent `guardrail_config` is provided, the factory returns NoOp hooks for all four positions — the default for development. In production, an agent's `guardrail_config` JSON drives which built-in or custom implementations are wired into each position. The same hook interface can also be extended with custom implementations for organization-specific needs.

---

## From hook events to the SIEM pipeline

Every hook execution produces structured security events that flow through a **SIEM pipeline** for compliance, alerting, and downstream integration:

```
Hook fires → SecurityEvent → SIEM Collector → Multiple exporters
                                              ├── Webhook (Slack, PagerDuty)
                                              ├── Syslog (RFC 5424)
                                              └── OCSF (Open Cybersecurity Schema)
```

- **`SecurityEvent`** — the normalized event written by hooks (action, agent, session, decision, severity, timestamp)
- **`ToolDecision`** — the structured outcome of a tool access check (allow / deny / require_approval), replacing the older `SecurityAudit` naming; persisted to PostgreSQL via `ToolDecisionModel`
- **`SecurityFinding`** — long-lived findings produced by the FindingEngine when a hook detects a policy violation; queryable via `GET /api/security/findings`
- **`AuditSink`** — pluggable destination for audit events with batched async writes and retention cleanup

The SIEM pipeline runs separately from hook execution so that hook latency is not coupled to downstream export latency. Hooks fire synchronously to enforce policy; the SIEM collector batches and exports asynchronously. This separation is what allows Hecate to enforce strict security at every LLM and tool boundary while still integrating with enterprise SIEM stacks.

---

## Risk levels and approval scopes

Hooks do not operate on raw strings alone — they have structured security metadata to work with. Every **Tool** and every **Agent** carries two security attributes:

- **Risk level** — `LOW`, `MEDIUM`, `HIGH`, or `CRITICAL`. Classifies the potential blast radius of the operation.
- **Approval scope** — `once`, `session`, `project`, or `global`. Defines how long an approval remains valid.

A `PreToolHook` can use these to make decisions: a `LOW`-risk tool with `session` approval runs automatically; a `CRITICAL`-risk tool with `once` approval pauses for human sign-off every single time. The combination gives operators fine-grained control over the autonomy/safety tradeoff per tool and per agent.

---

## Configuring hooks

Hooks are registered per worker via the agent's `guardrail_config` JSON field, which is consumed by `create_security_hooks(...)` to assemble the four hook positions. The default configuration uses no-op hooks (everything passes through), which is appropriate for development. Production deployments register concrete implementations — PII maskers, audit loggers, content filters — either from Hecate's built-in implementations (`InputSecurityHook`, `OutputSecurityHook`, `ToolResultSecurityHook`) or custom code.

The same hook interface powers both built-in features and custom extensions. You do not need a separate "plugin system" for security — the four hook types are the extension point.

---

## Middleware chain and tool policy

The hook points above each host an **ordered middleware chain**. Every interceptor that needs to act at the same position (PII detection, prompt-injection screening, DLP, content moderation, HITL clarification, custom checks) registers as a stage. Stages run in declared order; `BLOCK` short-circuits the chain and returns the originating stage's identity and reason; `SANITIZE` propagates `modified_data` to the next stage. SANITIZE without `modified_data` is treated as a contract violation and surfaced as BLOCK with the stage's identity — silent fall-through to ALLOW is forbidden.

Tool policy evaluation runs **inside** the `TOOL_PRE_EXECUTE` chain as the first stage, alongside other stages. The policy uses five-layer evaluation: built-in dangerous-pattern detection (shell-aware after [guardrail-upgrade-trio](../changes/guardrail-upgrade-trio/) T3), user rules with `arg_conditions`, workspace boundary, risk-level fallback, and sandbox routing. A `REQUIRE_APPROVAL` decision consults the wired `ApprovalCallback`. If no callback is configured, the call is denied with a `Tool requires approval but no callback configured` error — **fail-closed**, not fail-open.

---

## Coverage: every execution path

The chain runs on **both** production execution paths:

- **Pregel path** (`ToolWorker` / `LLMWorker` / `AgentExecutionPort`) — the chain is invoked inside the worker, around the real execution.
- **Direct tool loop** (path A in `api/v1/chat.py`) — the same chain runs around `tool_registry.execute(...)`. Both paths route through `services/security/guardrail_assembly.py::assemble_guardrails`, which reads the agent's `guardrail_config` and the workspace's `ToolPolicyModel` / `ToolPolicyRuleModel` rows.

Approval and turn boundaries are logged as `APPROVAL_ASKED` / `APPROVAL_DECIDED` / `TURN_START` / `TURN_END` events to the event store; rejected writes are logged as `CHANNEL_WRITE_REJECTED` (audit-only, fold-skipped). The `MONOTONIC.DENIAL` log invariant ensures a denial cannot be resurrected by a later stage.

---

## Further reading

- [Extension Points](../reference/extension-points.md) — the four guardrail hook ABCs (`PreLLMHook`, `PostLLMHook`, `PreToolHook`, `PostToolHook`) and their method signatures, plus the `ApprovalCallback` interface
- [Security Architecture](../design/security-architecture.md) — full L2 breakdown: guardrail hooks, PII anonymization, LLM Guard, JWT/API Key auth, audit trail
- [ADR-008: Security via Hooks](../design/adr/008-security-via-hooks.md) — the decision record explaining why hooks live in the engine
- [ADR-029 / ADR-030](../design/adr/) — chain semantics and event-log seam registry for the audit invariants
- [The Execution Engine](engine.md) — where hooks fit in the superstep loop
- [Configure SSO and SCIM](../how-to/configure-sso-scim.md) — wiring identity providers for production
