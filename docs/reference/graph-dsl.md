# Graph DSL Reference

The Graph DSL is a JSON format for defining Hecate agent workflows. A graph definition describes **channels** (state), **nodes** (execution units), and **edges** (control flow). The parser validates the JSON against a [JSON Schema](https://json-schema.org/) bundled inside the engine package, then the compiler performs structural validation before producing a `CompiledGraph` for the Pregel runtime.

- **Schema file**: `src/hecate/engine/graph-dsl.schema.json`
- **Parser**: `src/hecate/engine/graph_dsl.py` — `parse_graph(raw) → GraphConfig`
- **Compiler**: `src/hecate/engine/compiler.py` — `GraphCompiler.compile(config) → CompiledGraph`
- **Version**: `1.0` (the only accepted value)

---

## Top-level structure

```json
{
  "version": "1.0",
  "name": "My Workflow",
  "state": { ... },
  "nodes": { ... },
  "edges": [ ... ],
  "entry": "first_node"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `version` | string | Yes | Must be `"1.0"`. |
| `name` | string | Yes | Human-readable graph name (1–255 characters). |
| `state` | object | Yes | Channel declarations, keyed by channel name. See [Channels](#channels). |
| `nodes` | object | Yes | Node definitions, keyed by node ID. See [Node types](#node-types). |
| `edges` | array | Yes | Directed edges connecting nodes. See [Edges](#edges). |
| `entry` | string | No | The node ID where execution begins. If omitted, the graph is started via explicit node selection. |

---

## Channels

Channels are the state layer of a graph. Each channel has a **type** that determines write semantics, plus optional metadata for defaults, reduction, and persistence.

### Channel types

| Type | Write behavior | Typical use |
|------|---------------|-------------|
| `last_value` | Overwrites the previous value on each write. | Current context, single-valued state. |
| `topic` | Appends each written value to a list. | Message history, event logs. |
| `persistent_topic` | **Deprecated.** Auto-migrated to `topic` + `persistent: true`. | — |
| `accumulator` | Reduces values using a function (`"append"` or `"add"`). | Counters, running totals. |

> **`persistent_topic` is deprecated.** The parser logs a warning and automatically migrates it to `topic` with `persistent: true`. Use the `persistent` flag on any channel type instead.

### Channel properties

```json
"messages": {
  "type": "topic",
  "reduce": "append",
  "default": [],
  "initial": null,
  "persistent": true
}
```

| Property | Type | Applies to | Description |
|----------|------|------------|-------------|
| `type` | string | All | One of `last_value`, `topic`, `persistent_topic`, `accumulator`. **Required.** |
| `default` | any | All | Initial value set when the channel is first registered. |
| `initial` | any | `accumulator` | Starting value before the first reduction (e.g., `0` for an additive counter). |
| `reduce` | string | `topic`, `accumulator` | Reduction function: `"append"` or `"add"`. |
| `persistent` | boolean | All | Whether the channel survives across sessions via checkpointing. Orthogonal to write semantics — any type can be persistent. Default `false`. |

---

## Node types

Each node has a `type` and a `config` object. The `config` fields vary by type.

```json
"my_node": {
  "type": "conversation",
  "config": {
    "model": "gpt-4o",
    "system_prompt": "You are a helpful assistant.",
    "channels": { "readable": ["messages"], "writable": ["messages"] }
  }
}
```

> **`__start__` is a reserved node ID.** You cannot declare a node named `__start__` — it is a sentinel used only as an edge source.

### All node types

| Type | Description |
|------|-------------|
| `conversation` | Invokes an LLM with the current channel state as context. |
| `tool-call` | Executes a tool (builtin, custom, or MCP) and returns the result. |
| `condition` | Evaluates an expression against channel state to select which outgoing edge to follow. Supports three routing modes (see below). |
| `agent` | Delegates execution to a sub-agent. Supports direct invocation, tool-based invocation, and handoff. |
| `knowledge-retrieval` | Queries one or more knowledge bases via `EnginePort.knowledge_query()`. |
| `variable-set` | Sets or updates channel variables based on a static value or expression. |
| `suggestion` | Generates opening remarks or follow-up question suggestions. **Forbidden in `task` execution mode.** |
| `fan-out` | Dispatches multiple parallel branches concurrently (no worker invoked). Must have a reachable `merge` node downstream. |
| `merge` | Collects results from all branches of a preceding `fan-out` node. Must have an upstream `fan-out` node. |

### Common config fields

These fields appear in the `config` object of multiple node types:

| Field | Type | Applies to | Description |
|-------|------|------------|-------------|
| `model` | string | `conversation`, `agent` | LLM model identifier (e.g., `"gpt-4o"`). |
| `system_prompt` | string | `conversation`, `agent` | System prompt for the LLM call. |
| `channels.readable` | string[] | All | Channel names the node is allowed to read. |
| `channels.writable` | string[] | All | Channel names the node is allowed to write. |

### `condition` — routing modes

The `condition` node supports three routing modes via `config.routing_mode`:

| Mode | Description | Required config |
|------|-------------|-----------------|
| `condition` (default) | Expression-based routing. The `config.expression` is evaluated against channel state; the result selects the branch key. | `expression` |
| `intent` | Pattern matching with optional LLM intent classification. Regex patterns in `routing_config.intent_patterns` are matched first; if none match, an LLM classifies the intent. | `routing_config.intent_patterns` (non-empty array of `{pattern, target}`) |
| `dynamic` | An LLM selects the next speaker from candidate agents. | `routing_config.candidate_agents` (non-empty array of node IDs that exist in the graph) |

Additional `routing_config` options for `intent` and `dynamic` modes:

| Field | Type | Description |
|-------|------|-------------|
| `routing_prompt` | string | LLM prompt for routing classification. |
| `allow_repeated_speaker` | boolean | Whether the same agent may speak consecutively (`dynamic` mode only). Default `false`. |

### `agent` — invocation and handoff

The `agent` node has two config sections that control how the sub-agent is invoked and how context flows on handoff.

**Invocation mode** (`config.invocation_mode`):

| Value | Description |
|-------|-------------|
| `direct` (default) | Execute inline — the sub-agent runs as a nested graph or via `EnginePort.agent_execute()`. |
| `tool` | The sub-agent is exposed as a callable tool via `AgentDefinition`; the parent agent decides when to call it. |

**Handoff configuration** (`config.handoff`) — used when the agent node has outgoing `handoff` or `dynamic_handoff` edges:

| Field | Type | Description |
|-------|------|-------------|
| `context_mode` | string | How conversation history passes to the downstream agent: `inherited` (full history, default), `isolated` (fresh context), or `summarized` (collapsed summary). |
| `description` | string | Override description for the `handoff_to_agent` tool. Replaces auto-generated per-target descriptions. |

### `fan-out` and `merge`

```json
"fanout": {
  "type": "fan-out",
  "config": { "branches": ["analyst_a", "analyst_b", "analyst_c"] }
},
"merge": {
  "type": "merge",
  "config": {
    "fan_out_source": "fanout",
    "output_channel": "analysis_results"
  }
}
```

| Node | Config field | Description |
|------|-------------|-------------|
| `fan-out` | `branches` | Array of node IDs for the parallel branches. |
| `merge` | `fan_out_source` | Node ID of the upstream `fan-out` node. |
| `merge` | `output_channel` | Channel name where aggregated results are written. |

The compiler enforces a structural pairing: every `fan-out` must have at least one reachable `merge` node downstream, and every `merge` must have an upstream `fan-out`.

### `knowledge-retrieval`

| Config field | Type | Description |
|--------------|------|-------------|
| `kb_ids` | string[] | Knowledge base UUIDs to query. |
| `query_template` | string | Template for the query (may reference channel values). |
| `top_k` | integer | Maximum number of chunks to retrieve. |

### `tool-call`

| Config field | Type | Description |
|--------------|------|-------------|
| `tool_name` | string | Name of the tool to execute. |

### `variable-set`

| Config field | Type | Description |
|--------------|------|-------------|
| `variable_name` | string | Channel name to set. |
| `value` | any | The value to write (static or expression-derived). |

---

## Edges

Edges are ordered and directional. There are three edge forms:

### 1. Simple edge (unconditional)

```json
{ "source": "node_a", "target": "node_b" }
```

Execution flows from `source` to `target` unconditionally.

### 2. Conditional edge (branching)

```json
{
  "source": "check_category",
  "target": { "finance": "finance_agent", "tech": "tech_agent", "default": "general_agent" }
}
```

The `target` is an object mapping **route keys** to node IDs. At runtime, the source node (typically a `condition` node) evaluates its expression or routing logic and selects a key. The `"default"` key is used when no other key matches.

### 3. Handoff edge (agent control transfer)

```json
{ "source": "triage", "target": "billing", "type": "handoff" }
```

A handoff edge transfers control from one agent to another. Both source and target **must be `agent`-type nodes**. Handoff edges support two triggers:

| Trigger | How it appears in JSON | Description |
|---------|----------------------|-------------|
| `handoff` | `"type": "handoff"` or `"trigger": "handoff"` | Static handoff — the parent agent decides to transfer control. |
| `dynamic_handoff` | `"trigger": "dynamic_handoff"` | Dynamic handoff — used with `routing_mode: "dynamic"` for LLM-selected speaker transitions. |

> **No cycles in handoff chains.** The compiler rejects handoff subgraphs that form a cycle (A → B → C → A).

### Edge properties

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `source` | string | Yes | Node ID where the edge originates. |
| `target` | string \| object | Yes | Target node ID (simple/conditional), or a route-key-to-node-ID mapping (conditional). |
| `trigger` | string | No | Label or trigger type. `"handoff"` and `"dynamic_handoff"` have special semantics. |
| `type` | string | No | Edge type. Currently only `"handoff"` is defined; treated as an alias for `trigger`. |

---

## Sentinel node IDs

Two reserved node IDs have special meaning in edges:

| Sentinel | Role | Can appear as |
|----------|------|---------------|
| `__start__` | Graph entry point. | Edge **source** only. Cannot be declared as a node. |
| `__end__` | Graph termination. Reaching `__end__` ends execution. | Edge **target** only. |

A typical graph begins with an edge from `__start__` to the entry node, and terminal nodes have edges to `__end__`:

```json
{ "source": "__start__", "target": "entry_node" },
{ "source": "last_node", "target": "__end__" }
```

---

## Validation rules

The compiler (`GraphCompiler.compile()`) enforces these rules. Violations raise `GraphValidationError` with a `field` attribute pointing to the offending JSON path.

### Errors (block compilation)

| Rule | Description |
|------|-------------|
| **Entry point** | If `entry` is declared, it must reference an existing node ID. |
| **Edge references** | Every edge `source` and `target` must reference a declared node or a sentinel (`__start__`, `__end__`). Conditional edges validate each branch independently. |
| **Handoff endpoints** | Both `source` and `target` of `handoff`/`dynamic_handoff` edges must be `agent`-type nodes. |
| **Handoff acyclicity** | The handoff subgraph must not contain cycles. |
| **Fan-out/Merge pairing** | Every `fan-out` node must have at least one reachable `merge` downstream. Every `merge` node must have an upstream `fan-out`. |
| **Task mode restrictions** | In `task` execution mode, `suggestion` nodes are forbidden. |
| **Intent routing** | A `condition` node with `routing_mode: "intent"` must have a non-empty `routing_config.intent_patterns` array. |
| **Dynamic routing** | A `condition` node with `routing_mode: "dynamic"` must have a non-empty `routing_config.candidate_agents` array, and every candidate must be a declared node. |
| **Agent invocation mode** | If `invocation_mode` is present on an `agent` node, it must be `"direct"` or `"tool"`. |
| **Agent handoff config** | If `handoff.context_mode` is present on an `agent` node, it must be `"inherited"`, `"isolated"`, or `"summarized"`. |

### Warnings (do not block compilation)

| Rule | Description |
|------|-------------|
| **Unreachable nodes** | Nodes not reachable from `entry` via BFS are logged as warnings. The graph still compiles. |
| **Undeclared channel access** | If a node declares `channels.readable` or `channels.writable` referencing a channel not in `state`, a warning is logged. |

---

## Complete examples

### Conditional routing

A classifier agent categorizes input and routes to a specialist:

```json
{
  "version": "1.0",
  "name": "Conditional Pipeline",
  "state": {
    "messages": { "type": "topic", "reduce": "append" },
    "category": { "type": "last_value" }
  },
  "nodes": {
    "classifier": {
      "type": "agent",
      "config": {
        "agent_id": "",
        "system_prompt": "Classify the input as finance, tech, or legal. Write the category to the 'category' channel.",
        "channels": { "readable": ["messages"], "writable": ["messages", "category"] }
      }
    },
    "check_category": {
      "type": "condition",
      "config": { "expression": "category" }
    },
    "finance_agent": {
      "type": "agent",
      "config": {
        "agent_id": "",
        "system_prompt": "You are a finance specialist.",
        "channels": { "readable": ["messages"], "writable": ["messages"] }
      }
    },
    "general_agent": {
      "type": "agent",
      "config": {
        "agent_id": "",
        "system_prompt": "You are a general-purpose agent.",
        "channels": { "readable": ["messages"], "writable": ["messages"] }
      }
    }
  },
  "edges": [
    { "source": "__start__", "target": "classifier" },
    { "source": "classifier", "target": "check_category" },
    {
      "source": "check_category",
      "target": { "finance": "finance_agent", "default": "general_agent" }
    },
    { "source": "finance_agent", "target": "__end__" },
    { "source": "general_agent", "target": "__end__" }
  ],
  "entry": "classifier"
}
```

### Handoff (agent-to-agent control transfer)

A triage agent hands off to a specialist:

```json
{
  "version": "1.0",
  "name": "Customer Service Triage",
  "state": {
    "messages": { "type": "topic", "reduce": "append" }
  },
  "nodes": {
    "triage": {
      "type": "agent",
      "config": {
        "agent_id": "",
        "system_prompt": "Analyze the customer's request and hand off to the appropriate specialist.",
        "channels": { "readable": ["messages"], "writable": ["messages"] }
      }
    },
    "billing": {
      "type": "agent",
      "config": {
        "agent_id": "",
        "system_prompt": "You are a billing specialist.",
        "channels": { "readable": ["messages"], "writable": ["messages"] }
      }
    },
    "technical": {
      "type": "agent",
      "config": {
        "agent_id": "",
        "system_prompt": "You are a technical support specialist.",
        "channels": { "readable": ["messages"], "writable": ["messages"] }
      }
    }
  },
  "edges": [
    { "source": "__start__", "target": "triage" },
    { "source": "triage", "target": "billing", "type": "handoff" },
    { "source": "triage", "target": "technical", "type": "handoff" },
    { "source": "billing", "target": "__end__" },
    { "source": "technical", "target": "__end__" }
  ],
  "entry": "triage"
}
```

### Fan-out / Merge (parallel processing)

A researcher fans out to multiple analysts, results are merged, then summarized:

```json
{
  "version": "1.0",
  "name": "Fan-out Pipeline",
  "state": {
    "messages": { "type": "topic", "reduce": "append" },
    "research_data": { "type": "last_value" },
    "analysis_results": { "type": "last_value" }
  },
  "nodes": {
    "researcher": {
      "type": "agent",
      "config": {
        "agent_id": "",
        "system_prompt": "Gather relevant information and key points on the given topic.",
        "channels": { "readable": ["messages"], "writable": ["messages", "research_data"] }
      }
    },
    "fanout": {
      "type": "fan-out",
      "config": { "branches": ["analyst_a", "analyst_b"] }
    },
    "analyst_a": {
      "type": "agent",
      "config": {
        "agent_id": "",
        "system_prompt": "Analyze from a financial perspective.",
        "channels": { "readable": ["messages", "research_data"], "writable": ["messages"] }
      }
    },
    "analyst_b": {
      "type": "agent",
      "config": {
        "agent_id": "",
        "system_prompt": "Analyze from a technical perspective.",
        "channels": { "readable": ["messages", "research_data"], "writable": ["messages"] }
      }
    },
    "merge": {
      "type": "merge",
      "config": { "fan_out_source": "fanout", "output_channel": "analysis_results" }
    },
    "summarizer": {
      "type": "agent",
      "config": {
        "agent_id": "",
        "system_prompt": "Synthesize the analysis from multiple perspectives.",
        "channels": { "readable": ["messages", "analysis_results"], "writable": ["messages"] }
      }
    }
  },
  "edges": [
    { "source": "__start__", "target": "researcher" },
    { "source": "researcher", "target": "fanout" },
    { "source": "analyst_a", "target": "merge" },
    { "source": "analyst_b", "target": "merge" },
    { "source": "merge", "target": "summarizer" },
    { "source": "summarizer", "target": "__end__" }
  ],
  "entry": "researcher"
}
```

---

## See also

- **[Extension Points](extension-points.md)** — the 11 core + 4 SPI engine interfaces you can implement to customize execution.
- **[Engine Design](../design/engine-design.md)** — how the Pregel runtime executes a compiled graph superstep by superstep.
- **[Multi-Agent Orchestration Tutorial](../tutorials/04-multi-agent.md)** — build workflows with the six collaboration patterns using prebuilt templates.
- **[Graph DSL Schema](../../src/hecate/engine/graph-dsl.schema.json)** — the authoritative JSON Schema file.
- **[CLI Reference](cli.md)** — use `hecate workflow validate` to check a graph definition before deployment.
