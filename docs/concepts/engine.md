# The Execution Engine

Hecate's execution engine is a self-built runtime that turns a graph definition into executed work. It borrows the **Pregel** model from large-scale graph processing: execution proceeds in synchronized **supersteps**, with a barrier between each step where state is consolidated and persisted.

You do not need to understand the engine internals to use Hecate — but knowing the four core ideas (graphs, channels, supersteps, checkpoints) makes it clear how workflows run, how interruptions work, and how failed sessions recover.

---

## Why a custom engine

Most agent frameworks execute by chaining Python functions. Hecate takes a different approach: a workflow is a **data structure** (a JSON graph), and a separate **runtime** executes it. This separation has three consequences:

1. **The same graph runs in code and on the canvas.** The visual workflow builder and the Python SDK produce the same compiled graph. Neither is a wrapper around the other.
2. **State is explicit and serializable.** All intermediate state lives in typed channels, not in closure variables. This makes checkpointing and recovery possible.
3. **The engine has no framework dependencies.** The entire engine layer depends only on `jsonschema` for DSL validation. No LangChain, no LlamaIndex. This keeps it portable and testable.

---

## Graphs: the unit of work

A **graph** is a directed structure of **nodes** and **edges**. Each node is a typed execution unit:

| Node type | What it does |
|-----------|-------------|
| `conversation` | Calls an LLM with a system prompt, messages, and optional tools |
| `knowledge-retrieval` | Queries one or more knowledge bases and writes retrieved chunks to a channel |
| `tool-call` | Invokes a tool (built-in, custom, or MCP) and writes its result to a channel |
| `agent` | Delegates to another agent — either nested (`direct`) or as a callable tool (`tool`) |
| `condition` | Evaluates an expression and routes to one of several branches |
| `variable-set` | Writes a derived value to a channel based on an expression over current state |
| `fan-out` | Dispatches multiple branches to run in parallel |
| `merge` | Collects results from `fan-out` branches and writes a merged value |
| `suggestion` | Generates opening remarks or follow-up suggestions for the chat UI |

The workflow's entry and exit are not explicit node types. Execution begins at the special node ID `__start__` and ends when any edge reaches `__end__`; the workflow's input is the initial channel state, and the workflow's output is the final channel state at `__end__`.

Sandboxed code execution (running shell commands inside a Docker environment) is provided by the `tool-call` node type invoking the `exec_shell` tool — there is no separate `code` node type.

Edges connect nodes. An edge may be unconditional (always taken) or **conditional** (taken only when an expression evaluates to true). A `condition` node fans out to multiple targets based on its evaluation.

Graphs are written as JSON conforming to a [JSON Schema](../../src/hecate/runtime/graph-dsl.schema.json) bundled in the package. The visual canvas emits the same JSON; the Python SDK builds it programmatically. Both feed into the same compiler. See the [Graph DSL Reference](../reference/graph-dsl.md) for the complete schema.

---

## Channels: how state moves between nodes

Nodes do not pass arguments to each other directly. Instead, they read from and write to **channels** — typed state slots managed by the runtime. This is the key design choice that makes state explicit and serializable.

There are three channel types in active use, each with different write semantics. The JSON Schema also accepts a fourth legacy type (`persistent_topic`) which is auto-migrated to `topic` with `persistent: true` for backward compatibility.

| Channel type | Write behavior | Typical use |
|--------------|---------------|-------------|
| `last_value` | New value overwrites the old | Current plan, current status |
| `topic` | New messages append to a stream (with an optional reducer) | Conversation messages, tool call records |
| `accumulator` | New values fold into a running aggregate via a function | Iteration counter, token usage total |
| `persistent_topic` | *(deprecated)* — auto-migrated to `topic` with `persistent: true` | Legacy graphs only |

Orthogonal to write semantics, every channel may carry a `persistent: true` flag. When set, the channel's value survives across sessions by being included in the materialized cache (and thus in the event log's fold); when unset, it resets between executions. For example, a `topic` channel with `persistent: true` accumulates a permanent audit log; without `persistent`, it resets on each new session.

When a node executes, it receives a read-only snapshot of the channels it declared as inputs, and returns the values it wants to write. The runtime applies those writes according to each channel's semantics. A node never directly mutates another node's state.

This indirection means the runtime — not the nodes — owns all state. That is what makes event sourcing possible: every channel write is appended to the event log, and the materialized cache is just a replay fold of those events at a given point.

---

## Supersteps: the execution loop

The runtime executes a graph as a series of **supersteps**. Within each superstep, all ready nodes run, their writes are collected, and state is consolidated before the next superstep begins. This is the Bulk Synchronous Parallel (BSP) model.

```
┌─────────────────────────────────────────────┐
│  Superstep loop                              │
│                                              │
│  1. READ      — ready nodes read channels    │
│  2. DISPATCH  — nodes sent to the worker pool│
│  3. AWAIT     — wait for all workers         │
│  4. WRITE     — apply worker results         │
│  5. CHECKPOINT— persist state                │
│  6. ROUTE     — evaluate conditional edges   │
│  7. CHECK     — more ready nodes?            │
│                YES → back to step 1          │
│                NO  → done                    │
└─────────────────────────────────────────────┘
```

**Workers** are stateless. They receive a read-only channel snapshot, do their work (call an LLM, run code, invoke a tool), and return a result. The runtime applies the result to the channels. Because workers hold no state, they can run in-process, in separate threads, or (in principle) across processes — the runtime does not care.

The barrier between supersteps is where the runtime consolidates writes, appends to the event log (the single source of truth), and evaluates conditional edges to decide which nodes are ready next. This is also the only point where execution can be safely paused.

---

## Event log and checkpoints: durable, resumable state

Hecate is **event-sourced (Log-as-Truth)** — see [ADR-030](../design/adr/030-event-sourced-execution-state.md). The execution **event log** is the single source of truth for session state:

- After every superstep, the runtime appends channel writes and a `STEP_END` commit event in a single transaction. `STEP_END` (and `INTERRUPT`) are commit points; a torn tail (writes without a commit) rolls back on restore.
- **Checkpoints are a discardable materialized cache** — a snapshot of channel values plus the `log_version` cursor (the log position they were materialized at). They make recovery fast (no full log replay) but are never the source of truth: if a cache is lost, it is rebuilt by replaying the log.
- **Resume = cache + tail replay.** On resume, the runtime loads the cached channel state and replays only the events newer than the cache's `log_version`, through the same write path as live mutation. If no cache exists, it replays the full log.

This is what makes two features possible:

- **Time-travel debugging.** Every `STEP_END` commit point in the event log lets you inspect the exact state at any point in the execution. A failed run can be replayed from the log.
- **Resumable interruptions.** When a node calls `interrupt()` to pause for human approval, the runtime commits the log up to the interrupt and stops. When the user resumes (with a `Command`), the runtime derives the pause point from the log, injects the user's input, and continues the superstep loop from exactly where it stopped.

Sessions therefore have a lifecycle: `active` → `interrupted` → `active` → `completed` (or `failed`). An interrupted session does not hold resources; it is just a committed log tail waiting to be resumed.

Each user turn is bracketed by `TURN_START` / `TURN_END` events, and every approval request emits an `APPROVAL_ASKED` / `APPROVAL_DECIDED` pair inside that turn window (fail-closed: no answerer → denied, pair still emitted). On restore, the runtime runs the registered log invariants (`TOOL.PAIRING`, `APPROVAL.TURN_CLOSURE`, `MONOTONIC.DENIAL`) fail-stop — a log that violates them is treated as a bug signal, not replayed silently.

---

## Compilation: from JSON to executable

Before a graph can run, it passes through a **compiler** that validates and transforms the JSON definition:

1. **Schema validation** — node types, edge connections, and channel definitions are checked against the JSON Schema.
2. **Dependency analysis** — the compiler builds a node dependency graph and detects cycles.
3. **Channel binding** — each node's declared reads and writes are checked for type compatibility with the channels.
4. **Optimization** — dead nodes (unreachable from the entry point) are eliminated; parallel branches are detected.
5. **Emit `CompiledGraph`** — the output is a runtime object the Pregel loop can execute.

Unreachable nodes produce a warning, not an error — this lets work-in-progress graphs compile so you can test the reachable parts.

---

## How this maps to what you do

| You want to... | This concept is why... |
|----------------|-----------------------|
| Build a workflow on the canvas | The canvas emits the same JSON graph the engine compiles |
| Pause for human approval | `interrupt()` commits the event log and pauses; `Command` resumes from the log-derived pause point |
| Recover a crashed session | Replay the event log (cache + tail replay for speed) |
| Run nodes in parallel | The worker pool dispatches all ready nodes each superstep |
| Debug a failed run | Every `STEP_END` commit point in the event log is inspectable |
| Add a custom node type | Implement the `Worker` extension point (see [Extension Points](../reference/extension-points.md)) |

---

## Further reading

- [Engine Design](../design/engine-design.md) — full deep dive: compiler internals, channel manager, worker pool, streaming modes
- [Graph DSL Reference](../reference/graph-dsl.md) — node types, channel types, edge forms, and validation rules
- [Extension Points](../reference/extension-points.md) — the 26 engine ABCs + multiple plugin SPI types you can implement to customize execution
- [Agents and Execution Modes](agents.md) — how the `workflow` mode binds a graph to an agent
- [Guardrails and Hooks](guardrails.md) — where interception fits in the superstep loop
