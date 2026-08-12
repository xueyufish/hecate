# Sessions

A **session** is the runtime unit that holds the state of one conversation between a user (or upstream caller) and an agent. Sessions enable multi-turn conversations, time-travel debugging, resume after interruption, and audit trails.

This document explains the **conceptual model**: what a session is, how it relates to conversations and checkpoints, and how to choose the right state-storage backend. For implementation details, see [Engine Design](../design/engine-design.md). For multi-turn conversation usage, see [Tutorial: Build Your First Agent](../tutorials/01-first-agent.md).

---

## What is a session

A session is a **persistent runtime state** for a single agent execution thread. When a user sends a chat request:

1. Hecate creates (or resumes) a session
2. The agent runs, calling the LLM and tools
3. After each message turn, the session state is persisted
4. The next message in the same conversation resumes from that state

Without sessions, every request is **stateless** — the agent has no memory of previous messages. With sessions, the agent has full conversation memory plus the ability to **resume mid-execution** (e.g., after a human approval).

---

## Session lifecycle

Sessions go through four states:

```
   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
   │   active     │───▶│ interrupted  │───▶│   resumed   │
   │              │    │              │    │              │
   └──────┬───────┘    └──────┬───────┘    └──────┬───────┘
          │                   │                   │
          │                   │   ┌───────────────┘
          │                   │   │
          ▼                   ▼   ▼
        ┌───────────────────────────────────┐
        │       completed / error           │
        └───────────────────────────────────┘
```

| State | Meaning |
|---|---|
| `active` | Currently running — accepting new message turns |
| `interrupted` | Paused (e.g., waiting for human-in-the-loop approval) |
| `resumed` | (transient — re-enters `active` immediately) |
| `completed` | Terminal — task finished successfully |
| `error` | Terminal — task failed |

The transition `interrupted → resumed → active` is what enables **Human-in-the-Loop** (HITL): the agent pauses at an interrupt node, the human reviews via the UI, and execution continues from the saved checkpoint.

---

## Session vs conversation vs message

Three related concepts:

| Concept | What it is | Lifetime |
|---|---|---|
| **Conversation** | The user-facing thread (multiple sessions can share one conversation) | Indefinite (until user deletes) |
| **Session** | One runtime execution thread (multiple turns, may pause/resume) | Hours to days (configurable retention) |
| **Message** | A single exchange (user input → agent response) | Permanent |

```
Conversation 1: "Help me build a Python web app"
├── Session A: Initial planning (10 messages, completed)
├── Session B: Implementation round 1 (20 messages, interrupted for review)
└── Session C: Implementation round 2 (resumed from B, completed)
```

Why split them? A single conversation can span **multiple agent executions** (initial planning, then implementing, then debugging), each with its own state and checkpoints. Sessions are the runtime unit; conversations are the user-facing unit.

---

## Sessions and checkpoints

Every session is backed by a **checkpoint** — a serialized snapshot of all execution state at a point in time.

| Event | Checkpoint saved? |
|---|---|
| User sends a message | After agent finishes responding |
| Agent calls a tool | Before the tool call |
| PreLLM hook fires | Before sending to LLM |
| PostLLM hook fires | After receiving LLM response |
| Interrupt fires | Before pausing |
| Resume | After agent finishes the resumed run |

Checkpoints enable:

- **Time-travel debugging**: inspect state at any past step
- **Resume after crash**: replay from the last checkpoint
- **HITL approval**: pause, human reviews, resume from saved state

For implementation details, see [Engine Design > Checkpoints](../design/engine-design.md).

---

## Session state storage backends

Where session state lives is a deployment decision:

| Backend | Persistence | Multi-instance | Best for |
|---|---|---|---|
| **In-memory** (`memory`) | Lost on restart | ❌ Single instance only | Dev / test |
| **PostgreSQL** (`postgres`) | Durable | ✅ Multi-replica | Production default |
| **Redis** (`redis`) | Durable + fast | ✅ Multi-replica + fastest | Performance-critical |

**Default**: PostgreSQL. Use Redis when session throughput is the bottleneck and you can tolerate slightly less durability.

Set via `SESSION_STATE_STORE_BACKEND` env var.

---

## Multi-turn conversations with sessions

The OpenAI-compatible endpoint supports `session_id` for multi-turn:

```bash
# First message — creates session
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $HECATE_API_KEYS" \
  -d '{
    "model": "agent/abc-123",
    "messages": [{"role": "user", "content": "I have a Python app that crashes on startup"}],
    "session_id": "550e8400-e29b-41d4-a716-446655440000"
  }'

# Follow-up — same session, agent has memory
curl -X POST http://localhost:8000/v1/chat/completions \
  -d '{
    "model": "agent/abc-123",
    "messages": [{"role": "user", "content": "How do I check the logs?"}],
    "session_id": "550e8400-e29b-41d4-a716-446655440000"
  }'
```

The second request has memory of the first because both share the `session_id`. The agent understands "logs" refers to the Python app mentioned in turn 1.

---

## Time-travel debugging

Every checkpoint is queryable. To inspect state at a past step:

```bash
# List all checkpoints for a session
curl "http://localhost:8000/api/sessions/$SESSION_ID/checkpoints" \
  -H "Authorization: Bearer $ADMIN_KEY"

# Get a specific checkpoint's state
curl "http://localhost:8000/api/sessions/$SESSION_ID/checkpoints/$CHECKPOINT_ID" \
  -H "Authorization: Bearer $ADMIN_KEY"

# Roll back to a specific checkpoint (creates new session forked from it)
curl -X POST "http://localhost:8000/api/sessions/$SESSION_ID/replay/$CHECKPOINT_ID" \
  -H "Authorization: Bearer $ADMIN_KEY"
```

Use cases:

- **Debug**: "what was the agent thinking at step 7?"
- **Audit**: "prove what the LLM was told before it made decision X"
- **Recovery**: "replay from checkpoint 42 if today's deploy broke"

---

## Human-in-the-Loop via session interrupts

Sessions enable HITL approval workflows:

```
Agent runs → hits Interrupt node → session paused (state = interrupted)
                                          │
                                          ▼
                              Human reviews in UI / CLI
                                          │
                                          ▼
                         Approve / Reject / Edit
                                          │
                                          ▼
                Resume session → state = active → continues
```

Implementation:

```python
from hecate.engine.commands import interrupt, Command

async def risky_node(state):
    # Pause for human review
    decision = await interrupt(message="Approve sending $10k payment?")
    
    if decision == "approve":
        return {"action": "send_payment"}
    elif decision == "reject":
        return {"action": "abort"}
    else:
        return {"action": "edit", "new_amount": decision["new_amount"]}
```

The session is **durable** — even if the server restarts, the interrupt state survives. When a human approves, execution resumes from the saved checkpoint.

See [Tutorial: Human-in-the-Loop](../tutorials/06-human-in-the-loop.md) for hands-on.

---

## Session persistence and retention

| Aspect | Default | Configurable |
|---|---|---|
| Retention | 30 days after `completed` / `error` | `SESSION_RETENTION_DAYS` |
| Checkpoint granularity | Per message turn | (compile-time) |
| In-flight timeout | None (sessions live indefinitely) | `SESSION_IDLE_TIMEOUT_MINUTES` |

Expired sessions are deleted by a background process. **Audit events are NOT deleted** — they outlive the session for compliance.

---

## Sessions across the system

Sessions are referenced by:

| Component | How it uses sessions |
|---|---|
| **Chat completions** | Stores per-conversation context |
| **Tool calls** | Records tool calls within a session |
| **HITL** | Pauses / resumes sessions |
| **Audit log** | Every session event audited |
| **Workflows** | Each workflow execution is a session |
| **Evaluation** | Each eval run is a session |
| **A2A tasks** | Maps to A2A `Task` lifecycle |

Sessions are the **universal unit of work** across Hecate. If you understand sessions, you understand how the engine operates.

---

## Implementation references

- `src/hecate/models/session.py` — SessionModel + status enum + current_node + checkpoint_id
- `src/hecate/engine/checkpoint.py` — checkpoint persistence
- `src/hecate/services/session_state/` — session state store abstraction (memory / Postgres / Redis)
- `src/hecate/services/event_state/` — event sourcing for session transitions
- `src/hecate/engine/commands.py` — interrupt / Command APIs

## Related documents

- [Engine Design](../design/engine-design.md) — how sessions fit in the Pregel runtime
- [Memory System](memory.md) — what's in session memory vs persistent memory
- [Context Engineering](context-engineering.md) — how the engine decides what to include in each LLM call
- [Tutorial: Build Your First Agent](../tutorials/01-first-agent.md) — hands-on session usage
- [Tutorial: Human-in-the-Loop](../tutorials/06-human-in-the-loop.md) — session interrupts
- [Reference: Data Models](../reference/data-models.md) — SessionModel schema
- [Architecture Center](../design/) — broader architecture