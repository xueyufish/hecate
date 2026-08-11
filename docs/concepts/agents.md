# Agents and Execution Modes

An **Agent** is Hecate's core execution unit — an autonomous entity configured with a persona, a model, tools, knowledge, and memory. When a user sends a message, the agent decides how to respond based on its configuration and its **execution mode**.

Understanding the three execution modes is the single most important concept in Hecate. The mode determines what runs when a chat request arrives: a single LLM call, a preset three-stage pipeline, or a custom graph.

---

## What makes up an Agent

Every agent carries the same set of configurable parts, regardless of mode:

| Part | Purpose | Example |
|------|---------|---------|
| **Persona** | The system prompt that defines the agent's character and instructions | "You are a security analyst who answers questions about audit logs." |
| **Model config** | Primary model, optional fallback, and inference parameters (temperature, max tokens) | `gpt-4o` with temperature 0.2, fallback to `gpt-4o-mini` |
| **Tools** | Capabilities the agent can invoke — built-in, custom, or discovered via MCP | Web search, code execution, a custom HTTP tool |
| **Knowledge bases** | Document collections the agent can search via RAG retrieval | An internal wiki, a product manual |
| **Memory blocks** | Named regions in the context window the agent can read and edit | `user_profile`, `current_task` |
| **Risk level** | Security classification governing how the agent may operate | `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` |

These parts are attached at the agent level so the same configuration works across all three modes. The mode controls *how* they are used, not *whether* they are available.

---

## The three execution modes

### `chat` — single LLM call

The simplest mode. When a message arrives, Hecate assembles the conversation context, runs a single LLM call with the agent's persona and tools, and returns the response. Tools and knowledge bases are available, but there is no multi-step planning.

```
User message → [Assemble context + LLM call] → Response
```

**Use it when:** you want direct Q&A, single-task automation, or a ChatGPT-like experience backed by your own tools and knowledge.

This is the default mode and what the [first agent tutorial](../tutorials/01-first-agent.md) uses.

### `three_layer` — Guard → Plan → Execute

A preset pipeline that adds structure to complex single-agent tasks. Each incoming message passes through three stages:

1. **Guard** — security check and risk assessment. Decides whether the request is safe to process and classifies its risk level.
2. **Plan** — task decomposition and skill selection. Breaks the request into steps and picks the right skills and tools for each.
3. **Execute** — the sub-agent carries out the plan, calling tools and producing the response.

```
User message → [Guard] → [Plan] → [Execute] → Response
                   │          │          │
                   └──────────┴──────────┘
                        feedback loop
```

A condition node evaluates after each execution pass and decides whether to loop back to planning (if the task is incomplete) or finish.

**Use it when:** a single LLM call is not enough and you want built-in safety checks and planning, but you do not need the full flexibility of a custom workflow.

### `workflow` — custom graph

The most powerful mode. The agent is bound to a **Workflow** — a directed graph you define via the visual canvas or the JSON DSL. Each node in the graph is a typed execution unit (`conversation`, `knowledge-retrieval`, `tool-call`, `agent`, `condition`, `variable-set`, `fan-out`, `merge`, `suggestion`). Edges connect nodes and may carry conditional expressions for branching.

```
User message → [Compile graph] → Pregel superstep loop → Response
                                    ┌─────────────────────────┐
                                    │ Node A (conversation)   │
                                    │ Node B (knowledge-     │
                                    │           retrieval)   │
                                    │ Node C (tool-call)     │
                                    │ Node D (condition)     │
                                    │ Node E (agent)         │
                                    └─────────────────────────┘
```

Workflows are versioned, support durable checkpoints, and can implement any topology — sequential pipelines, parallel fan-out, handoff routing, broadcast discussions, negotiation, and debate. See [The Execution Engine](engine.md) for how the runtime executes these graphs.

**Use it when:** you need multi-agent coordination, structured decision flows, or any topology the preset three-layer pipeline does not cover. The [multi-agent tutorial](../tutorials/04-multi-agent.md) walks through the six collaboration patterns.

---

## Choosing a mode

| If you need... | Use |
|----------------|-----|
| Direct Q&A with optional tools and knowledge | `chat` |
| Built-in safety checks and planning for a single agent | `three_layer` |
| Multiple agents, conditional branching, custom topologies | `workflow` |
| A simple retry loop around a single LLM call | `chat` with a small workflow |

All three modes share the same engine runtime, checkpoint system, and guardrail hooks. Moving from `chat` to `three_layer` to `workflow` is an increase in structure, not a change in platform — your tools, knowledge bases, and memory blocks carry over.

---

## Further reading

- [The Execution Engine](engine.md) — how the Pregel runtime runs graphs as supersteps
- [Core Concepts: Agent](../design/concepts.md#agent) — the full entity definition with all fields
- [Build Your First Agent](../tutorials/01-first-agent.md) — hands-on tutorial using `chat` mode
- [Multi-Agent Orchestration](../tutorials/04-multi-agent.md) — building `workflow`-mode agents
