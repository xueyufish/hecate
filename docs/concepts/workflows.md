# Workflows

A single LLM call answers a question. But when an agent must *decide* what to do — check a knowledge base, call a tool, branch on the result, delegate to a sub-agent, run branches in parallel, and merge the outcomes — you need more than a prompt. You need a **graph**: a directed set of nodes that execute in order, with state flowing between them.

Hecate's `workflow` execution mode lets you define that graph as JSON. The engine parses it, validates it, compiles it, and runs it through the same [Pregel runtime](engine.md) that powers every other mode. This page explains the mental model — what the building blocks are, how they fit, and when you'd choose `workflow` over `chat` or `three_layer`. For the full field-by-field JSON spec, see the [Graph DSL Reference](../reference/graph-dsl.md).

---

## Three building blocks

Every workflow is three things — **channels** (state), **nodes** (execution), and **edges** (control flow):

```
┌─────────────────────────────────────────────────────────────┐
│  GRAPH                                                       │
│                                                              │
│  CHANNELS (state)     NODES (execution)      EDGES (flow)   │
│  ┌──────────┐         ┌──────────────┐      ┌→  node_b  ─┐  │
│  │ messages │◄────────│ conversation │◄─────┤            │  │
│  │ (topic)  │────────►│              │      └→  cond    ─┤  │
│  ├──────────┤         ├──────────────┤                    │  │
│  │ result   │◄────────│ tool-call    │◄───────────────────┘  │
│  │ (last_v) │         ├──────────────┤                       │
│  │          │         │ condition    │── true  ──┐           │
│  │          │         │              │── false ──┼──┐        │
│  │          │         └──────────────┘          │  │        │
│  │          │                                   │  │        │
│  └──────────┘                                   │  │        │
│              entry: conversation ───────────────┘  │        │
└────────────────────────────────────────────────────┼────────┘
                                                     │
                                              (parallel branch)
```

### Channels — the state layer

Channels hold state between nodes. Each channel has a **type** that controls what happens when a node writes to it:

| Type | Write behavior | Use for |
|------|---------------|---------|
| `last_value` | Overwrites previous value | Current answer, single-valued state |
| `topic` | Appends to a list | Message history, event log |
| `accumulator` | Reduces via a function (`append` / `add`) | Counters, running totals |

Any channel can be marked `persistent: true` to survive across sessions via [checkpoints](engine.md#checkpoints). The deprecated `persistent_topic` type auto-migrates to `topic` + `persistent: true`.

### Nodes — the execution units

Nodes do the actual work. Each node has a `type` that determines what runs when the Pregel loop dispatches to it. Nine types ship today, grouped by purpose:

| Group | Node type | What it does |
|-------|-----------|-------------|
| **LLM** | `conversation` | Calls an LLM with the current channel state as context |
| **Tools** | `tool-call` | Executes a [tool](tools-and-mcp.md) (builtin, custom, or MCP) |
| **Knowledge** | `knowledge-retrieval` | Queries [knowledge bases](knowledge-rag.md) via `EnginePort.knowledge_query()` |
| **Branching** | `condition` | Evaluates an expression to pick which outgoing edge to follow |
| **State** | `variable-set` | Sets a channel variable to a static value or expression |
| **UX** | `suggestion` | Generates opening remarks or follow-up suggestions (forbidden in `task` mode) |
| **Sub-agents** | `agent` | Delegates to a sub-agent — supports direct, tool-based, and handoff invocation |
| **Parallelism** | `fan-out` | Dispatches parallel branches (must have a downstream `merge`) |
| **Parallelism** | `merge` | Collects results from a preceding `fan-out` |

Every node declares which channels it `readable` and `writable` — this is how the engine enforces data flow boundaries between nodes.

### Edges — the control flow

Edges connect nodes. A simple edge runs the target after the source completes. A **conditional edge** carries an expression that the engine evaluates at runtime to pick one of several targets — this is how `condition` nodes implement branching. The reserved source `__start__` marks the graph's entry point.

---

## From JSON to execution: the compilation pipeline

A workflow JSON passes through three stages before it runs:

```
raw JSON
   │
   ▼  parse_graph(raw) → GraphConfig       engine/graph_dsl.py
   │  JSON Schema validation (graph-dsl.schema.json)
   │  persistent_topic → topic migration
   │
   ▼  GraphCompiler.compile(config)         engine/compiler.py
   │  Structural validation:
   │    • entry node exists
   │    • fan-out has a reachable merge
   │    • suggestion not used in task mode
   │    • handoff edges only between agent nodes
   │  Unreachable-node detection (BFS from entry, WARN log)
   │
   ▼  CompiledGraph → PregelRuntime          engine/pregel.py
   │  Superstep loop executes ready nodes,
   │  dispatches to workers, drains channels at barriers
```

The parser produces a `GraphConfig`; the compiler produces a `CompiledGraph` ready for the runtime. The runtime treats every workflow the same way — as a graph of nodes exchanging state through channels at superstep barriers.

---

## Workflow mode in context

Hecate's [three execution modes](agents.md) — `chat`, `three_layer`, `workflow` — share the same engine, checkpoint system, and [guardrails](guardrails.md). The difference is structure:

| Mode | Structure | When to use |
|------|-----------|-------------|
| `chat` | Single LLM call | Direct Q&A, single-task automation |
| `three_layer` | Preset Guard → Plan → Execute pipeline | Built-in safety + planning without custom topology |
| `workflow` | Your graph (any topology) | Multi-agent coordination, conditional branching, parallel branches |

Moving from `chat` to `three_layer` to `workflow` is an increase in structure, not a change in platform. Your [tools](tools-and-mcp.md), [knowledge bases](knowledge-rag.md), and [memory blocks](memory.md) carry over.

---

## Six collaboration patterns as graph templates

For multi-agent scenarios, Hecate ships pre-built graph templates (`engine/templates.py`) covering the common collaboration topologies. Each is a static graph you can instantiate and customize:

| Pattern | Topology | Example use case |
|---------|----------|-----------------|
| **Hierarchical** | Supervisor dispatches to specialized sub-agents | Customer service: triage agent → billing / tech / FAQ agents |
| **Handoff** | One agent transfers control to another (no return) | Specialist escalation: generalist → expert |
| **Pipeline** | Sequential chain, each agent refines the previous output | Research → draft → edit → fact-check |
| **Broadcast** | Same input to multiple agents in parallel | Get multiple perspectives on one question |
| **Negotiation** | Agents iterate toward consensus | Multi-stakeholder decision making |
| **Debate** | Structured opposition with a judge agent | Adversarial quality review |

All six are unified as graph templates — they are not separate execution mechanisms. The `engine/patterns.py` module detects which pattern a given graph matches. See the [multi-agent tutorial](../tutorials/04-multi-agent.md) for hands-on examples.

---

## Choosing what to build

| You need... | Use |
|-------------|-----|
| A single LLM response | `chat` mode — no workflow needed |
| Built-in safety checks + planning | `three_layer` mode |
| Conditional branching | `workflow` with `condition` nodes |
| Multiple specialist agents coordinating | `workflow` with `agent` nodes, or a [collaboration template](#six-collaboration-patterns-as-graph-templates) |
| Parallel execution branches | `workflow` with `fan-out` + `merge` |
| A preset multi-agent topology without writing JSON | Instantiate a template from `engine/templates.py` |
| State that survives session interruption | Any mode — [checkpoints](engine.md) are built-in |

---

## Further reading

- [Graph DSL Reference](../reference/graph-dsl.md) — the full JSON spec: every field, every node type, every channel property
- [The Execution Engine](engine.md) — the Pregel/BSP runtime that executes compiled graphs
- [Agents and Execution Modes](agents.md) — how `workflow` relates to `chat` and `three_layer`
- [Tools, MCP, and A2A](tools-and-mcp.md) — what the `tool-call` node invokes
- [Knowledge and Retrieval](knowledge-rag.md) — what the `knowledge-retrieval` node queries
- [ADR-001: Graph-First Orchestration](../design/adr/001-graph-first-orchestration.md) — why Hecate chose graphs as the universal orchestration primitive
- [ADR-007: Multi-Agent as Graph Templates](../design/adr/007-multi-agent-as-graph-templates.md) — why all six collaboration patterns are graphs
- [Multi-Agent Orchestration tutorial](../tutorials/04-multi-agent.md) — hands-on with the six collaboration patterns
