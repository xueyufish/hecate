# Tutorial: Human-in-the-Loop

> **20 minutes** — Add approval checkpoints, content reviews, and human redirection to your workflows using `interrupt()` and `Command`. Resume sessions from exact pause points and inspect state with the durable event log.

Every long-running agent eventually faces a moment where it should stop and ask: *should I really do this?* Hecate handles this with two cooperating primitives — `interrupt()` to pause execution and `Command` to resume it. Together they make human-in-the-loop (HITL) workflows first-class: not a wrapper around the LLM, but part of the engine itself.

---

## What you will learn

- How `interrupt()` pauses execution and persists state
- How `Command` resumes a session from the pause point
- How to build an **approval node** for `HIGH` and `CRITICAL` risk operations
- How to design workflows that route differently based on user input
- How to inspect the resumed session's state and traces

## Prerequisites

- Hecate running locally — see [Quickstart](../getting-started/quickstart.md)
- At least one LLM provider configured in `.env`
- `hecate` CLI on your `PATH`
- Completed [Build Your First Agent](01-first-agent.md)

Throughout this tutorial we use `dev-key-change-me` as the API key. Replace it with your actual `HECATE_API_KEYS` value.

---

## Why this lives in the engine

Most frameworks implement HITL by wrapping the LLM client or polling an external queue. Hecate takes a different approach: pause points live inside the [Pregel runtime](../concepts/engine.md), so every interruption commits the execution event log and every resume replays from the log-derived pause point (cache + tail replay).

The practical consequences:

- A crashed server does not lose the pending approval — the interrupt commit point is in the execution event log.
- Time-travel debugging works across HITL pauses — you can replay the log from any point before or after a human decision.
- The same workflow can be reviewed, paused, redirected, and resumed by different operators without changing the agent code.

> **Tool-approval vs workflow-interrupt.** The `interrupt()` mechanism above pauses a *workflow* and waits. Tool-level approval (a `REQUIRE_APPROVAL` decision from the tool access policy) is **fail-closed** instead of waiting: if no approval backend is configured, the tool call is denied on the spot — and the `APPROVAL_ASKED` / `APPROVAL_DECIDED` audit pair is still written to the event log, enclosed by the turn's `TURN_START` / `TURN_END`. A `once`-scoped grant is consumed on first use, and a denied `tool_call_id` cannot be resurrected later in the session (`MONOTONIC.DENIAL`). See [Guardrails — Middleware chain and tool policy](../concepts/guardrails.md).

The two primitives that make this work are:

| Primitive | Lives in | Purpose |
|-----------|---------|---------|
| `interrupt(value)` | Engine runtime | Pause execution and return `value` to the caller as the reason |
| `Command(goto=, update=, interrupt=, return_value=)` | Engine types | Resume control — jump to a node, inject state, or signal completion |

Both are log-safe: a `Command` carries the user's decision and is replayable against the event log at any historical `STEP_END` commit point.

---

## Step 1 — Create an approval workflow

We will build a workflow that drafts a marketing email, asks for human approval, and either sends or revises based on the decision. Save this as `approval-workflow.json`:

```json
{
  "version": "1.0",
  "name": "email-approval",
  "state": {
    "messages": { "type": "topic", "reduce": "append" },
    "draft": { "type": "last_value" },
    "decision": { "type": "last_value" },
    "status": { "type": "last_value" }
  },
  "nodes": {
    "draft_email": {
      "type": "conversation",
      "config": {
        "model": "auto",
        "system_prompt": "You are a marketing copywriter. Write a concise email for the user's request."
      }
    },
    "ask_human": {
      "type": "tool-call",
      "config": {
        "tool_name": "ask_human_approval",
        "tool_args": {
          "draft": "{{ draft }}",
          "prompt": "Approve sending this email?"
        },
        "output_channel": "decision"
      }
    },
    "send_email": {
      "type": "tool-call",
      "config": {
        "tool_name": "send_email",
        "tool_args": "{{ draft }}",
        "output_channel": "send_result"
      }
    },
    "revise_email": {
      "type": "conversation",
      "config": {
        "model": "auto",
        "system_prompt": "Revise the email based on the human's feedback."
      }
    },
    "route_decision": {
      "type": "condition",
      "config": {
        "expression": "state.decision in ('approve', 'revise', 'reject')"
      }
    }
  },
  "edges": [
    { "source": "__start__", "target": "draft_email" },
    { "source": "draft_email", "target": "ask_human" },
    { "source": "ask_human", "target": "route_decision" },
    {
      "source": "route_decision",
      "target": {
        "approve": "send_email",
        "revise": "revise_email",
        "reject": "__end__"
      }
    },
    { "source": "revise_email", "target": "ask_human" },
    { "source": "send_email", "target": "__end__" }
  ]
}
```

The key node is `ask_human`: it invokes the `ask_human_approval` tool, whose worker code calls `interrupt({...})` with a payload describing what the human is being asked to review. The runtime pauses there, commits the event log up to the interrupt point, and waits for a `Command`.

Because the DSL is declarative — it describes *what* runs, not *how* — the `interrupt()` call lives in the tool's Python worker, not in the workflow JSON. Here is what the tool worker looks like:

```python
from hecate.engine.command import interrupt


def ask_human_approval(draft: str, prompt: str) -> dict:
    """Pause execution and wait for a human decision on the draft."""
    user_decision = interrupt({
        "draft": draft,
        "prompt": prompt,
        "options": ["approve", "revise", "reject"],
    })
    return {
        "decision": user_decision,
        "status": "awaiting_review",
    }
```

The tool's worker is just a Python function that takes the tool's input arguments and returns a dict that the runtime writes to the configured channel. The `interrupt()` call inside it is what causes the runtime to pause — it is a runtime-level primitive, available in any worker code, that returns the user's `Command` payload as its return value when execution resumes.

For the schema details on `tool-call` nodes, see the [Graph DSL Reference](../reference/graph-dsl.md#tool-call). For how `interrupt()` is detected and translated to a paused session, see [Engine Design: interrupt and Command](../design/engine-design.md#human-in-the-loop-interrupt-and-command).

---

## Step 2 — Run the workflow up to the approval

Start the workflow with the CLI. The execution will pause at `ask_human` and return an interrupt payload:

```bash
hecate workflows run approval-workflow.json \
  --input "Write an email announcing our Q3 product launch to existing customers"
```

Output:

```json
{
  "session_id": "01HXYZ...",
  "status": "interrupted",
  "current_node": "ask_human",
  "interrupt": {
    "draft": "Subject: Q3 Launch — Now Live\n\nHi {first_name},\n\n...",
    "prompt": "Approve sending this email?"
  },
  "checkpoint_id": "ckpt_01HX..."
}
```

Note three things:

1. `status` is `interrupted`, not `failed` — execution paused cleanly.
2. `current_node` shows exactly where the pause happened.
3. `checkpoint_id` identifies the materialized cache — the interrupt commit point in the event log is the durable record; the cache just makes resume fast. You can return to this point later even if the server restarts.

The CLI keeps the `session_id` and `checkpoint_id` for the resume step.

---

## Step 3 — Approve, reject, or revise

Use `hecate workflows resume` with a `Command` to continue execution. The decision is mapped to the route keys declared in the conditional edges (`approve` / `revise` / `reject`).

### Approve and send

```bash
hecate workflows resume 01HXYZ... \
  --goto send_email \
  --update '{"decision": "approve"}'
```

The workflow jumps to `send_email`, the `send_email` tool runs, and the session completes.

### Reject and stop

```bash
hecate workflows resume 01HXYZ... \
  --return-value "Email rejected by reviewer"
```

The session terminates with the rejection reason as the final return value. The workflow does not run any further nodes.

### Request a revision

```bash
hecate workflows resume 01HXYZ... \
  --goto revise_email \
  --update '{"decision": "revise", "feedback": "Make the subject line punchier and trim the second paragraph"}'
```

The `revise_email` node reads the feedback, produces a new draft, and routes back to `ask_human` — the human gets to review again. This loop continues until the human chooses `approve` or `reject`.

---

## Step 4 — Inspect the session

Every pause and resume writes to the audit trail and the trace store. Inspect the session to see exactly what happened:

```bash
hecate sessions show 01HXYZ...
```

Output includes:

- **Timeline** — every superstep with timestamps and which node ran
- **Channel state at each commit point** — the log fold at any `STEP_END` (this is the time-travel view)
- **All interrupt payloads** — what the human saw at each pause
- **All `Command` payloads** — what the human decided at each resume
- **Tool calls and their results** — including the `send_email` invocation if it ran

You can also fetch a specific checkpoint's cached state:

```bash
hecate checkpoints show ckpt_01HX... --pretty
```

This is the foundation of post-hoc review: every approval decision is auditable, replayable, and traceable to the exact reasoning state the agent had at the moment.

---

## Step 5 — Programmatic resume via API

The CLI is convenient for manual review, but most production systems route approvals through a UI or a Slack bot. The same `Command` payload works over the API:

```bash
curl -X POST http://localhost:8000/api/sessions/01HXYZ.../resume \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "command": {
      "goto": "send_email",
      "update": { "decision": "approve", "reviewer": "alice@example.com" }
    }
  }'
```

The `Command` shape matches the engine type:

| Field | Purpose |
|-------|---------|
| `goto` | Jump to a specific node, bypassing edge resolution |
| `update` | Merge additional channel values before the next superstep begins |
| `interrupt` | (returned by workers) Pause execution and surface payload to caller |
| `return_value` | Signal that the graph has produced its final output |

Each `Command` is recorded with the reviewer's identity, enabling dual-audit trails (the agent's reasoning + the human's decision).

---

## Patterns to remember

| Pattern | How to use it |
|---------|---------------|
| **Tool risk gating** | Call `interrupt()` inside a `PreToolHook` for `HIGH` / `CRITICAL` risk tools — the same approval flow works at the hook level |
| **Content review** | Place an approval node after a generation step; reject routes to `__end__`, revise loops back |
| **Multi-stakeholder approval** | Call `interrupt()` once per approver; the workflow serializes the pauses until all decisions are collected |
| **Timeout handling** | Set `session_ttl` on the workflow — abandoned sessions are garbage-collected after the TTL |
| **Asynchronous resume** | The pause is durable; the resume can come hours or days later from a different process or operator |

---

## What you built

A workflow that pauses for human review at an interrupt point, commits state durably to the event log, and resumes via a typed `Command`. The same primitives scale to multi-step approvals, tool risk gating, and asynchronous review systems — all without changing the engine or the agent code.

---

## Further reading

- [The Execution Engine](../concepts/engine.md) — how `interrupt` and `Command` fit in the superstep loop
- [Guardrails and Hooks](../concepts/guardrails.md) — using `interrupt()` inside `PreToolHook` for risk-based approval
- [Engine Design: interrupt and Command](../design/engine-design.md#human-in-the-loop-interrupt-and-command) — the full runtime contract
- [Multi-Agent Orchestration](04-multi-agent.md) — building approval flows into multi-agent workflows