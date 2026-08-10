# Graph DSL Reference

The Hecate Graph DSL is a JSON format for describing agent workflows as directed graphs. A graph defines **nodes** (work to do), **edges** (control flow between nodes), and **state** (the channels that carry data between nodes). The Pregel runtime compiles and executes the graph as a Bulk Synchronous Parallel loop.

This page is the canonical reference for the DSL. The authoritative source is the JSON Schema at [`src/hecate/engine/graph-dsl.schema.json`](../../src/hecate/engine/graph-dsl.schema.json), bundled inside the `hecate` package and used to validate every graph definition at parse time.

> **Hands-on first?** Read [Tutorial 04: Multi-Agent Orchestration](../tutorials/04-multi-agent.md) for end-to-end examples of generating, validating, and running graphs. This page is the lookup reference.

---

## Top-level structure

Every graph is a JSON object with five required fields and one optional field.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `version` | string | Yes | DSL version. Must be `"1.0"`. |
| `name` | string | Yes | Graph name. 1–255 characters. |
| `state` | object | Yes | Channel declarations. Maps channel name → channel definition. May be empty `{}`. |
| `nodes` | object | Yes | Node definitions. Maps node ID → node definition. Node IDs must not equal `__start__` (reserved). |
| `edges` | array | Yes | Ordered list of edges. May be empty `[]`. |
| `entry` | string | No | Entry-point node ID. If omitted, the compiler infers it from the graph topology. |

### Minimal example

```json
{
  "version": "1.0",
  "name": "Echo",
  "state": {
    "messages": {"type": "topic", "reduce": "append"}
  },
  "nodes": {
    "echo": {
      "type": "agent",
      "config": {
        "agent_id": "00000000-0000-0000-0000-000000000001",
        "channels": {"readable": ["messages"], "writable": ["messages"]}
      }
    }
  },
  "edges": [
    {"source": "echo", "target": "__end__"}
  ],
  "entry": "echo"
}
```

### Reserved node IDs

| ID | Meaning |
|----|---------|
| `__start__` | Virtual entry point. The compiler injects it automatically — do **not** declare a node with this ID (the schema rejects it). |
| `__end__` | Virtual exit point. Reference it as an edge target to terminate execution. |

---

## State: channels

The `state` object declares the channels that carry data between nodes. Each channel has a type and optional configuration.

### Channel types

| Type | Semantics | When to use |
|------|-----------|-------------|
| `last_value` | Holds the most recent value written. New writes overwrite previous ones. | Scalar state: current step, latest decision, accumulated answer. |
| `topic` | Append-only log. Combines writes via a `reduce` function. | Conversation message history, audit event streams. |
| `persistent_topic` | **Deprecated.** Auto-migrated at parse time to `topic` with `persistent: true`. | Use `topic` + `persistent: true` instead. |
| `accumulator` | Reduces writes into a single value via the configured `reduce` function. | Counters, aggregations, running totals. |

### Channel definition fields

| Field | Type | Required | Applies to | Description |
|-------|------|----------|------------|-------------|
| `type` | string | Yes | all | One of the four channel types above. |
| `default` | any | No | all | Default value when no value has been written. |
| `initial` | any | No | all | Initial value seeded at graph start. |
| `reduce` | string | No | `topic`, `accumulator` | Reduction function: `"append"` (list append) or `"add"` (numeric addition). |
| `persistent` | boolean | No | all | Whether the channel survives across sessions (i.e., is included in checkpoints). Orthogonal to write semantics. Default `false`. |

### Examples

```json
"state": {
  "messages": {
    "type": "topic",
    "reduce": "append"
  },
  "current_step": {
    "type": "last_value",
    "default": "init"
  },
  "turn_count": {
    "type": "accumulator",
    "initial": 0,
    "reduce": "add",
    "persistent": true
  }
}
```

> **Persistence is orthogonal to type.** A `last_value` channel with `persistent: true` is included in checkpoints and restored on session resume. The channel type controls how concurrent writes combine within a single superstep; `persistent` controls cross-session lifetime.

---

## Nodes

Each entry under `nodes` maps a node ID to a node definition. The node ID is the map key (a string you choose); the definition specifies the `type` and a `config` block.

```json
"nodes": {
  "<node_id>": {
    "type": "<node_type>",
    "config": { ... }
  }
}
```

### Node types

| Type | Purpose | Key config fields |
|------|---------|-------------------|
| [`conversation`](#conversation) | Single LLM turn within the graph | `model`, `system_prompt`, `channels` |
| [`tool-call`](#tool-call) | Invoke a named tool | `tool_name`, `channels` |
| [`condition`](#condition) | Branch on an expression or LLM routing | `expression`, `routing_mode`, `routing_config` |
| [`agent`](#agent) | Delegate to another agent (sub-graph) | `agent_id`, `invocation_mode`, `handoff`, `channels` |
| [`knowledge-retrieval`](#knowledge-retrieval) | RAG query against a knowledge base | `kb_ids`, `query_template`, `top_k`, `channels` |
| [`variable-set`](#variable-set) | Set a channel to a literal or computed value | `variable_name`, `value`, `channels` |
| [`fan-out`](#fan-out) | Spawn parallel branches | `branches` |
| [`merge`](#merge) | Wait for parallel branches and aggregate | `fan_out_source`, `output_channel` |
| [`suggestion`](#suggestion) | Emit suggested follow-up prompts | (none required) |

### Common config fields

These fields appear in multiple node types:

| Field | Type | Applies to | Description |
|-------|------|------------|-------------|
| `channels.readable` | array of strings | most types | Channel names this node reads from. |
| `channels.writable` | array of strings | most types | Channel names this node writes to. |
| `model` | string | `conversation`, `agent` | LLM model override for this node (otherwise inherits from the agent or graph). |
| `system_prompt` | string | `conversation`, `agent` | System prompt override for this node's LLM calls. |

---

### `conversation`

Runs a single LLM turn, reading input from `readable` channels and writing the response to `writable` channels.

```json
"greet": {
  "type": "conversation",
  "config": {
    "model": "gpt-4o-mini",
    "system_prompt": "You are a friendly greeter. Respond in one sentence.",
    "channels": {
      "readable": ["messages"],
      "writable": ["messages"]
    }
  }
}
```

---

### `tool-call`

Invokes a registered tool by name. Tool arguments come from the channel state or the node config; the result is written to the writable channels.

```json
"lookup_user": {
  "type": "tool-call",
  "config": {
    "tool_name": "read_file",
    "channels": {
      "readable": ["file_path"],
      "writable": ["file_contents"]
    }
  }
}
```

| Config field | Type | Description |
|--------------|------|-------------|
| `tool_name` | string | Required. The registered tool name (e.g. `"web_search"`, `"read_file"`, `"execute_code"`). Use `hecate tool list` to discover available tools. |

---

### `condition`

Routes to different downstream nodes based on a boolean expression, an intent pattern, or a dynamic LLM selection. The routing behavior is controlled by `routing_mode`.

#### Routing modes

| `routing_mode` | Mechanism | Required config |
|----------------|-----------|-----------------|
| `condition` (default) | Evaluates a Python expression against the channel state. The expression must return a truthy or falsy value. | `expression` |
| `intent` | Matches the latest user message against regex patterns. Each pattern maps to a target node. | `routing_config.intent_patterns` |
| `dynamic` | An LLM picks the next speaker from a pool of candidate agents. | `routing_config.candidate_agents`, optional `routing_prompt` |

#### `routing_config` fields

| Field | Type | Applies to | Description |
|-------|------|------------|-------------|
| `intent_patterns` | array | `intent` | List of `{pattern, target}` objects. `pattern` is a regex; `target` is a node ID. |
| `candidate_agents` | array of strings | `dynamic` | Candidate agent node IDs the LLM may choose from. |
| `routing_prompt` | string | `dynamic` | Optional override for the LLM classification prompt. |
| `allow_repeated_speaker` | boolean | `dynamic` | Whether the same agent may speak on consecutive turns. Default `false`. |

#### Examples

Condition mode (the most common):

```json
"exit_check": {
  "type": "condition",
  "config": {
    "expression": "'APPROVED' in state.messages[-1].content"
  }
}
```

Intent mode:

```json
"router": {
  "type": "condition",
  "config": {
    "routing_mode": "intent",
    "routing_config": {
      "intent_patterns": [
        {"pattern": "refund|cancel", "target": "billing_agent"},
        {"pattern": "password|login", "target": "auth_agent"}
      ]
    }
  }
}
```

Dynamic mode:

```json
"moderator": {
  "type": "condition",
  "config": {
    "routing_mode": "dynamic",
    "routing_config": {
      "candidate_agents": ["scientist", "skeptical_reviewer"],
      "routing_prompt": "Pick the next speaker based on the latest message.",
      "allow_repeated_speaker": false
    }
  }
}
```

> **Edges determine targets.** A `condition` node itself does not encode its targets — they live in the outgoing edges. See [Edges](#edges) below for how conditional routing is expressed on the edge side.

---

### `agent`

Delegates execution to another agent by ID. The invoked agent runs as a nested sub-graph (its own `mode`, tools, guardrails). Two invocation modes control how the delegation appears to the parent graph.

| Config field | Type | Description |
|--------------|------|-------------|
| `agent_id` | string (UUID) | Required. The UUID of the agent to invoke. |
| `invocation_mode` | string | `"direct"` (default) executes inline as a nested sub-graph. `"tool"` exposes the agent as a callable tool the parent can decide to invoke. |
| `system_prompt` | string | Optional system prompt override for this delegation. |
| `model` | string | Optional model override. |
| `handoff` | object | Optional handoff configuration (see below). |
| `channels` | object | Readable/writable channel bindings. |

#### Handoff configuration

When an `agent` node has outgoing edges of `type: "handoff"`, the `handoff` block controls how conversation history is transferred.

| Field | Type | Values | Description |
|-------|------|--------|-------------|
| `context_mode` | string | `inherited` (default), `isolated`, `summarized` | `inherited` passes the full conversation history. `isolated` starts the downstream agent with a fresh context. `summarized` collapses the history into a compact summary before handoff. |
| `description` | string | — | Optional override for the `handoff_to_agent` tool description shown to the parent LLM. |

#### Example

```json
"specialist": {
  "type": "agent",
  "config": {
    "agent_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "invocation_mode": "direct",
    "handoff": {
      "context_mode": "summarized",
      "description": "Hand off to the billing specialist for refund questions."
    },
    "channels": {
      "readable": ["messages"],
      "writable": ["messages"]
    }
  }
}
```

---

### `knowledge-retrieval`

Runs a RAG query against one or more knowledge bases and writes the retrieved chunks to the writable channels.

| Config field | Type | Description |
|--------------|------|-------------|
| `kb_ids` | array of strings | Required. Knowledge base UUIDs to query. |
| `query_template` | string | Template string for the query. May reference channel values. |
| `top_k` | integer | Number of chunks to retrieve per knowledge base. |

```json
"lookup_docs": {
  "type": "knowledge-retrieval",
  "config": {
    "kb_ids": ["11111111-1111-1111-1111-111111111111"],
    "query_template": "{state.messages[-1].content}",
    "top_k": 5,
    "channels": {
      "readable": ["messages"],
      "writable": ["retrieved_context"]
    }
  }
}
```

---

### `variable-set`

Sets a channel to a literal value. Useful for initializing flags or injecting constants into the state.

| Config field | Type | Description |
|--------------|------|-------------|
| `variable_name` | string | The channel name to set. Must also appear in `channels.writable`. |
| `value` | any | The literal value (string, number, boolean, object, array). |

```json
"init_state": {
  "type": "variable-set",
  "config": {
    "variable_name": "current_step",
    "value": "greeting",
    "channels": {"writable": ["current_step"]}
  }
}
```

---

### `fan-out`

Spawns parallel branches. Each branch executes concurrently in the next superstep.

| Config field | Type | Description |
|--------------|------|-------------|
| `branches` | array of strings | Required. Node IDs of the parallel branch heads. |

```json
"scatter": {
  "type": "fan-out",
  "config": {
    "branches": ["researcher_a", "researcher_b", "researcher_c"]
  }
}
```

---

### `merge`

Waits for all branches spawned by a `fan-out` node and aggregates their outputs into a single channel.

| Config field | Type | Description |
|--------------|------|-------------|
| `fan_out_source` | string | Required. The node ID of the upstream `fan-out` node. |
| `output_channel` | string | Required. Channel name to write the aggregated result to. |

```json
"gather": {
  "type": "merge",
  "config": {
    "fan_out_source": "scatter",
    "output_channel": "combined_findings"
  }
}
```

> **Pair `fan-out` with `merge`.** Every `fan-out` should have a corresponding `merge` downstream. Without one, parallel branches terminate independently and the engine cannot synchronize their results.

---

### `suggestion`

Emits suggested follow-up prompts based on the current conversation state. No required config.

```json
"suggest_next": {
  "type": "suggestion",
  "config": {}
}
```

---

## Edges

Edges define control flow. They are an array of objects under the top-level `edges` field, evaluated in declaration order.

### Edge fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `source` | string | Yes | The node ID this edge leaves from. |
| `target` | string **or** object | Yes | The destination. A string is a single node ID. An object maps trigger values to node IDs (conditional routing). |
| `trigger` | string or null | No | A label or predicate for this edge. Useful when the source is a `condition` node and you want to document the branch condition. |
| `type` | string | No | Edge type. Currently only `"handoff"` is defined; omits to `None` for normal transitions. |

### Edge forms

**Simple edge** — unconditional transition:

```json
{"source": "writer", "target": "critic"}
```

**Conditional edge map** — when `target` is an object, the keys are matched against the evaluated result of the source node (typically a `condition` node's expression):

```json
{
  "source": "exit_check",
  "target": {
    "true": "__end__",
    "false": "writer"
  }
}
```

In this form, the source `condition` node evaluates its expression, and the result (`"true"` or `"false"`, or any other string value) selects which target to follow.

**Edge to `__end__`** — terminates execution:

```json
{"source": "critic", "target": "__end__"}
```

**Handoff edge** — for multi-agent control transfer:

```json
{"source": "triage", "target": "specialist", "type": "handoff"}
```

A `handoff` edge is the runtime signal that control transfers to the target agent node with the context semantics configured in the target's `handoff` block.

---

## Putting it together: a writer–critic loop

This graph pairs a writer agent with a critic agent. The critic either approves the draft (route to `__end__`) or sends it back for revision (route back to `writer`).

```json
{
  "version": "1.0",
  "name": "Writer-Critic Loop",
  "state": {
    "messages": {"type": "topic", "reduce": "append"}
  },
  "nodes": {
    "writer": {
      "type": "agent",
      "config": {
        "agent_id": "<writer-agent-uuid>",
        "system_prompt": "You draft content based on the brief.",
        "channels": {"readable": ["messages"], "writable": ["messages"]}
      }
    },
    "critic": {
      "type": "agent",
      "config": {
        "agent_id": "<critic-agent-uuid>",
        "system_prompt": "You critique drafts. If the draft is acceptable, reply with APPROVED. Otherwise, list specific improvements.",
        "channels": {"readable": ["messages"], "writable": ["messages"]}
      }
    },
    "exit_check": {
      "type": "condition",
      "config": {
        "expression": "'APPROVED' in state.messages[-1].content"
      }
    }
  },
  "edges": [
    {"source": "writer", "target": "critic"},
    {"source": "critic", "target": "exit_check"},
    {"source": "exit_check", "target": {"true": "__end__", "false": "writer"}}
  ],
  "entry": "writer"
}
```

---

## Validation

The engine validates every graph against the bundled JSON Schema at parse time. Validation runs automatically when you create or update a workflow via the API or CLI; you can also trigger it explicitly.

### CLI

```bash
hecate workflow validate path/to/graph.json
```

### Common validation errors

| Error | Cause | Fix |
|-------|-------|-----|
| `Unknown node type '<x>'` | A node `type` is not one of the 9 valid values. | Use only types listed in [Node types](#node-types). |
| `Invalid routing_mode '<x>'` | A `condition` node has a bad `routing_mode`. | Use `condition`, `intent`, or `dynamic`. |
| `Cycle without exit` (compiler) | A loop has no path to `__end__`. | Add a `condition` node whose true-branch targets `__end__`. |
| `Missing channels` (compiler) | An `agent` or `conversation` node has no `readable`/`writable` channels. | Declare `channels.readable` and `channels.writable` for every node that produces or consumes state. |
| `Bad edge references` (compiler) | An edge `source` or `target` references a non-existent node ID. | Edge endpoints must reference declared node IDs, `__start__`, or `__end__`. |
| `Persistent topic deprecation warning` | A channel uses `type: "persistent_topic"`. | Migrate to `type: "topic"` with `persistent: true`. Auto-migrated at parse time. |

Schema-level errors carry a `field` attribute pointing to the offending JSON path (e.g. `nodes.critic.config.model`), so you can localize the problem in your DSL file.

---

## See also

- [Tutorial 04: Multi-Agent Orchestration](../tutorials/04-multi-agent.md) — generate, validate, and run graphs end-to-end.
- [Engine Design](../design/engine-design.md) — how the Pregel runtime compiles and executes graphs.
- [ADR 007: Multi-Agent as Graph Templates](../design/adr/007-multi-agent-as-graph-templates.md) — the rationale for the six built-in collaboration patterns.
- [ADR 019: Visual Workflow Node Types](../design/adr/019-visual-workflow-node-types.md) — how the React Flow canvas maps to these node types.
- [Bundled JSON Schema](../../src/hecate/engine/graph-dsl.schema.json) — the authoritative source of truth.
