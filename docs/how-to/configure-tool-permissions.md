# How to Configure Tool Permissions

Every tool an agent can invoke is a potential security boundary. Hecate gives you three layers of control: **workspace-level baseline policies** that apply to every agent in the workspace, **per-agent rules** that override or extend the baseline, and **per-agent allow/deny lists** that whitelist or blacklist specific tools for one agent.

This guide walks through each layer and shows how they compose into a defense-in-depth policy.

---

## Prerequisites

- `admin` role on the target Workspace (for workspace-level rules)
- `editor` role (for per-agent configuration)
- An agent you want to apply a policy to (or create one with `hecate agents create`)

---

## The three layers

| Layer | Scope | Overrides by | Set via |
|-------|-------|-------------|---------|
| **Workspace baseline** | All agents in the workspace | Can be overridden by per-agent rules | `hecate tool-policies create` |
| **Per-agent rules** | One agent | Overrides workspace baseline | `hecate tool-policies rules create --agent-id` |
| **Per-agent mode + lists** | One agent | Mode changes interpretation of lists | `hecate agent-policy-config set` |

The layers compose: a tool call is evaluated against all matching rules, and the highest-priority matching rule wins.

---

## Layer 1 — Workspace-level baseline

Workspace admins define **security baselines** that no agent can weaken. The most common pattern is "deny everything dangerous, allow only what we explicitly approve":

```bash
# Deny all shell-execution tools by default
hecate tool-policies create \
  --action deny \
  --tool-pattern "exec_shell*" \
  --priority 100 \
  --description "Block shell exec — workspace baseline"

# Deny all destructive DB operations
hecate tool-policies create \
  --action deny \
  --tool-pattern "db_*" \
  --priority 100 \
  --arg-conditions '{"action": "drop|truncate|delete_*"}' \
  --description "Block destructive DB ops"

# Allow safe web search everywhere
hecate tool-policies create \
  --action allow \
  --tool-pattern "web_search" \
  --priority 50 \
  --description "Allow web search globally"
```

Three actions are available:

| Action | Effect |
|--------|--------|
| `allow` | The tool runs without prompting |
| `deny` | The tool call is blocked; the agent sees an error |
| `ask` | The tool pauses for human approval via `interrupt()` (see [Human-in-the-Loop tutorial](../tutorials/05-human-in-the-loop.md)) |

The `tool-pattern` is a glob matched against tool names. `priority` resolves conflicts — higher-priority rules win when multiple match.

Workspace baselines cannot be overridden by per-agent configurations with `allow` actions. A workspace `deny` is a hard wall; an agent can only narrow further, never widen.

---

## Layer 2 — Per-agent rules

Override or extend the baseline for one agent. Common uses: enable a tool the workspace baseline denies, or add an `ask` rule for a sensitive tool the agent has access to:

```bash
# Allow the SQL agent to use exec_shell (it has been audited)
hecate tool-policies rules create \
  --agent-id "$(hecate agents show data-analyst --format id)" \
  --action allow \
  --tool-pattern "exec_shell" \
  --priority 200 \
  --arg-conditions '{"command": "psql .*"}' \
  --description "Allow psql only"

# Require approval for any file-write tool used by this agent
hecate tool-policies rules create \
  --agent-id "$(hecate agents show report-writer --format id)" \
  --action ask \
  --tool-pattern "file_write*" \
  --priority 200
```

`arg-conditions` matches against tool arguments (as key-value pairs). The example above allows `exec_shell` only when the command starts with `psql`. Other shell commands are still denied by the workspace baseline.

Rule precedence: the engine evaluates **all matching rules** (workspace + per-agent) and picks the highest-priority match. If two rules have the same priority, the more specific one (per-agent) wins.

---

## Layer 3 — Per-agent mode + lists

A third layer applies a coarse-grained mode and explicit lists. The mode controls how the lists are interpreted:

| Mode | Behavior |
|------|----------|
| `default` | Lists are ignored; rule-based policies apply as normal |
| `restricted` | Only tools in `tool_allowlist` may be invoked; everything else is denied |
| `audit` | Same as `default` but every tool call is logged to the audit trail regardless of outcome |

Set the mode for an agent:

```bash
hecate agent-policy-config set \
  --agent-id "$(hecate agents show data-analyst --format id)" \
  --mode restricted \
  --tool-allowlist "web_search,sql_query,exec_shell"
```

In `restricted` mode, the agent can only invoke the three named tools — any attempt to invoke a tool outside the allowlist is denied immediately. This is the strongest mode and appropriate for tightly-scoped agents.

In `audit` mode, the rule-based policy decides whether a call is allowed, denied, or requires approval — but every call is logged to the audit trail with the full context (arguments, decision, agent, session). Use this for agents handling sensitive data where you want full traceability regardless of policy outcome.

---

## Putting it together

Here is an example composition for a "data analyst" agent in a regulated workspace:

```bash
# Workspace baseline (set once by admin)
hecate tool-policies create --action deny --tool-pattern "exec_*" --priority 100
hecate tool-policies create --action deny --tool-pattern "file_delete*" --priority 100
hecate tool-policies create --action ask --tool-pattern "file_write*" --priority 100

# Per-agent overrides for the data analyst
hecate tool-policies rules create --agent-id $DATA_ANALYST --action allow --tool-pattern "exec_sql" --priority 200
hecate tool-policies rules create --agent-id $DATA_ANALYST --action deny  --tool-pattern "exec_shell" --priority 200

# Per-agent mode
hecate agent-policy-config set --agent-id $DATA_ANALYST --mode restricted \
  --tool-allowlist "web_search,exec_sql,file_read"
```

Result for the data analyst agent:

| Tool | Effective policy | Why |
|------|------------------|-----|
| `web_search` | Allow | In the per-agent allowlist and not denied by baseline |
| `exec_sql` | Allow | Per-agent rule priority 200 overrides baseline deny on `exec_*` |
| `exec_shell` | Deny | Per-agent deny rule priority 200 + baseline deny |
| `file_read` | Allow | In the per-agent allowlist |
| `file_write*` | Ask (human approval) | Workspace baseline; no per-agent override |
| `file_delete*` | Deny | Workspace baseline |

The agent can read files and query the database freely, must request human approval to write anything, and is hard-blocked from destructive operations. The combination gives both flexibility for legitimate work and protection against accidents or misuse.

---

## Step-by-step checklist

To configure tool permissions for a new agent:

1. **Identify the agent's tool needs.** What does it need to read, query, write, or execute?
2. **Set the workspace baseline** if you have admin access — block destructive patterns first.
3. **Add per-agent rules** to extend the baseline — typically allowing specific tools or asking for specific categories.
4. **Choose a mode**: `default` for most agents, `restricted` for tightly-scoped, `audit` for sensitive workloads.
5. **Test with a dry run** — see below.
6. **Monitor the audit log** for the first few days of operation.

---

## Dry run: see what would happen

Before enforcing a policy, dry-run it against a real session:

```bash
hecate tool-policies dry-run \
  --agent-id $AGENT_ID \
  --tools "exec_shell,file_write,web_search" \
  --args '{"file_write": {"path": "/etc/passwd"}}'
```

Output:

```json
{
  "evaluations": [
    {
      "tool": "exec_shell",
      "decision": "deny",
      "matched_rule": "workspace-baseline-deny-exec",
      "priority": 100
    },
    {
      "tool": "file_write",
      "decision": "ask",
      "matched_rule": "workspace-baseline-ask-file_write",
      "priority": 100
    },
    {
      "tool": "web_search",
      "decision": "allow",
      "matched_rule": "per-agent-allow-web_search",
      "priority": 200
    }
  ]
}
```

This shows exactly which rule matched and what the resulting decision is, without actually invoking anything. Use it to verify your policy before deploying.

---

## Inspecting decisions in the audit log

Every policy decision is logged to the audit trail. Find decisions for a specific session:

```bash
hecate audit list --type tool_decision --session $SESSION_ID
```

Each entry contains:

- The tool name and arguments
- The decision (allow / deny / ask)
- The matched rule and its priority
- The agent, user, and session context
- A SHA-256 hash of the arguments (for privacy-preserving dedup)

These decisions flow into the [SIEM pipeline](../concepts/guardrails.md#from-hook-events-to-the-siem-pipeline) for export to your existing security stack (Slack alerts, Syslog, OCSF).

---

## Common patterns

| Pattern | Recipe |
|---------|--------|
| **Read-only agent** | `mode: restricted`, allowlist of read-only tools |
| **Coding assistant** | Allow `file_read`, `exec_shell`; deny `file_delete*`; ask for `file_write*` |
| **Database admin** | Allow `exec_sql`; ask for any tool with `drop|truncate|delete_*` args |
| **Customer-facing chatbot** | Deny all shell exec; allow `web_search`, `kb_query`; mode `audit` for full traceability |
| **Multi-tenant setup** | Per-workspace baseline + per-tenant agent rules; admin reviews cross-tenant decisions in audit log |

---

## API reference

The CLI commands above map to these REST endpoints:

| CLI | REST |
|-----|------|
| `hecate tool-policies list` | `GET /api/tool-policies` |
| `hecate tool-policies create` | `POST /api/tool-policies` |
| `hecate tool-policies dry-run` | `POST /api/tool-policies/dry-run` |
| `hecate tool-policies rules create` | `POST /api/tool-policy-rules` |
| `hecate agent-policy-config set` | `PUT /api/agents/{id}/policy-config` |
| `hecate audit list --type tool_decision` | `GET /api/security/decisions` |

---

## Further reading

- [Guardrails and Hooks](../concepts/guardrails.md) — how `PreToolHook` calls into the policy engine
- [Human-in-the-Loop tutorial](../tutorials/05-human-in-the-loop.md) — what happens when a tool is matched by an `ask` rule
- [Tool Risk Levels](../concepts/agents.md) — the `risk_level` attribute on every tool
- [ADR-008: Security via Hooks](../design/adr/008-security-via-hooks.md) — the architectural rationale