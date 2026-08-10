# Context Engineering

Every LLM has a fixed context window. Long conversations, large tool outputs, and verbose knowledge retrieval all compete for that budget. Naive truncation drops the wrong messages; naive inclusion blows the token limit and degrades reasoning quality. **Context Engineering** is Hecate's answer: a pipeline that assembles the right context for every LLM call, within budget, with the most relevant material prioritized.

This is not a single feature you toggle. It is a set of cooperating components that run before every LLM invocation inside the execution engine. Understanding what they do helps you predict how your agent will behave in long sessions and how to tune it.

---

## The problem

An agent in a long session accumulates state: the conversation history, tool call results, retrieved knowledge chunks, memory blocks, and the current plan. Left unmanaged, this state grows until it exceeds the model's context window. At that point one of three things happens, all bad:

- The API rejects the request with a token-limit error.
- The model's reasoning quality degrades as irrelevant context crowds out the signal.
- Old context is silently truncated, losing information the agent still needs.

Hecate's context pipeline addresses all three by treating the context window as a **managed budget** — every component that contributes to the final prompt is weighed against the available tokens.

---

## The pipeline

Before each LLM call, the engine runs the assembled context through a pipeline. Each stage transforms the context, and the final output is what the model actually sees.

```
┌──────────────────────────────────────────────────────────┐
│  Raw state                                               │
│  (messages, memory blocks, tool results, KB chunks)      │
│                          │                               │
│  ┌───────────────────────▼───────────────────────┐       │
│  │ Phase Detector                                │       │
│  │ Classifies the current task phase             │       │
│  │ (e.g., planning, execution, reflection)       │       │
│  └───────────────────────┬───────────────────────┘       │
│                          │                               │
│  ┌───────────────────────▼───────────────────────┐       │
│  │ Evidence Tracker                              │       │
│  │ Normalizes tool execution results into        │       │
│  │ structured evidence the LLM can cite          │       │
│  └───────────────────────┬───────────────────────┘       │
│                          │                               │
│  ┌───────────────────────▼───────────────────────┐       │
│  │ Token Budget Manager                          │       │
│  │ Allocates the context-window budget across    │       │
│  │ system prompt, memory, history, evidence,     │       │
│  │ tools — enforces a hard ceiling               │       │
│  └───────────────────────┬───────────────────────┘       │
│                          │                               │
│  ┌───────────────────────▼───────────────────────┐       │
│  │ Message Prioritizer                           │       │
│  │ Ranks messages by relevance and recency;      │       │
│  │ drops the lowest-ranked when over budget      │       │
│  └───────────────────────┬───────────────────────┘       │
│                          │                               │
│  ┌───────────────────────▼───────────────────────┐       │
│  │ Tool Filter                                   │       │
│  │ Selects which tools to expose based on the    │       │
│  │ phase and plan, reducing tool-choice noise    │       │
│  └───────────────────────┬───────────────────────┘       │
│                          │                               │
│  ┌───────────────────────▼───────────────────────┐       │
│  │ Provider Shaping                              │       │
│  │ Adapts the final context to the specific      │       │
│  │ model's format and capabilities               │       │
│  └───────────────────────┬───────────────────────┘       │
│                          │                               │
│  ┌───────────────────────▼───────────────────────┐       │
│  │ Context Assembler                             │       │
│  │ Combines everything into the final prompt     │       │
│  │ sent to the LLM                               │       │
│  └───────────────────────┬───────────────────────┘       │
│                          ▼                               │
│  Final prompt (within budget)                            │
└──────────────────────────────────────────────────────────┘

When messages are dropped during assembly to stay within budget, the **Context Offloader** (run by the LLMWorker pipeline before the assembler) writes them to the AgentEnvironment filesystem as JSON and inserts a reference stub — see [Context offloading](#context-offloading-preserve-what-compression-would-lose) below.
```

---

## What each stage does

### Phase Detector

Classifies what the agent is currently doing — planning, executing, reflecting, answering. The phase influences downstream stages: during planning, the system prompt and plan get more budget; during execution, evidence and tool results get more.

### Evidence Tracker

When a tool runs, its raw output is often verbose or unstructured. The Evidence Tracker normalizes tool results into **structured evidence** — concise, citation-friendly records the LLM can reference. This keeps tool output from consuming disproportionate context while preserving the information the agent needs.

### Token Budget Manager

The context window is divided into allocations: system prompt, memory blocks, conversation history, evidence, and tool definitions. The Budget Manager enforces a hard ceiling — if the total exceeds the model's limit, lower-priority sections are compressed or dropped first. This is the component that prevents token-limit errors.

### Message Prioritizer

Not all messages in a long conversation are equally useful. The Prioritizer ranks messages by relevance (semantic similarity to the current task) and recency, then drops the lowest-ranked when the history allocation is over budget. This is smarter than FIFO truncation — an important earlier instruction survives even if many messages have passed since.

### Tool Filter

An agent may have dozens of tools available, but only a few are relevant to the current phase. Exposing all of them wastes context tokens and degrades the model's tool-selection accuracy. The Tool Filter selects the relevant subset based on the phase and plan.

### Provider Shaping

Different LLM providers have different prompt formats, system-message conventions, and tool-calling schemas. Provider Shaping adapts the final context to the specific model being called, so a workflow that works with GPT-4o also works with Claude or a local Ollama model without per-provider code.

### Context Assembler

The final stage. Combines the filtered, prioritized, budgeted context into the actual prompt sent to the LLM. The Assembler is the single component that produces the final API call.

---

## Conversation compression (L2 memory)

The pipeline above runs per LLM call. Separately, Hecate applies a **compression pipeline** to conversation history as it grows, so that long sessions do not accumulate unbounded context. This is part of the [Memory System](memory.md) (L2: Conversation Memory):

```
Context window filling
    │
    ├── 1. Snip         — remove the oldest low-value messages
    ├── 2. Microcompact — summarize small groups of messages
    └── 3. Autocompact   — generate a running summary of the conversation
```

Compression activates progressively as the window fills, so a conversation can run for hundreds of turns without hitting the limit while retaining its most important context.

---

## Context offloading (preserve what compression would lose)

Compression is lossy: once a message is summarized, its full content is gone. For sessions where the agent might need to refer back to old content — long-running support conversations, code reviews, multi-step research — losing detail is unacceptable.

Hecate resolves this with **Context Offloading**: when the LLMWorker pipeline drops messages to fit the token budget, the dropped messages are written to the AgentEnvironment filesystem as JSON files, and a compact **reference stub** replaces them in the live context. The agent can retrieve the full content on demand via the existing `read_file` tool — no new tool registration required.

```
LLMWorker pipeline (5 steps)
    │
    ├── 1. Truncation  — shorten oversized tool results
    ├── 2. Estimation  — measure token cost
    ├── 3. Selection   — keep the most recent N messages within budget
    ├── 4. Offload     — write the dropped prefix to the environment
    │                      as memory/sessions/{session_id}/offloaded_{timestamp}.json
    │                      and insert a reference stub
    └── 5. Compression — last resort, only if stub + selected still exceed budget
```

The offload step happens **before** compression because offloading preserves the full original content, while compression is lossy. Offloading is skipped when no environment is attached (`is_enabled()` returns `False`) or when the dropped-token count is below the configured threshold (`CONTEXT_OFFLOAD_THRESHOLD_TOKENS`).

The reference stub contains a short topic summary plus the file path, so the LLM knows the content exists and how to fetch it. From the agent's perspective, this looks like a normal message referencing a file; from the runtime's perspective, the dropped messages are recoverable on demand rather than lost.

---

## Why this matters for your agents

| Situation | What the pipeline does |
|-----------|-----------------------|
| A long support conversation | Message Prioritizer + compression keep the session within budget |
| An agent with 30 tools | Tool Filter exposes only the 4 relevant to the current phase |
| A tool returns a 5,000-line log | Evidence Tracker condenses it to a citation |
| Switching from GPT-4o to a local model | Provider Shaping adapts the prompt format |
| A workflow that runs 50 supersteps | Budget Manager prevents cumulative context bloat |

You do not configure the pipeline per agent — it runs automatically. But knowing it exists helps you understand why an agent in a long session stays coherent, why tool output doesn't crowd out the system prompt, and why the same workflow works across providers.

---

## Further reading

- [Engine Design](../design/engine-design.md) — where the context pipeline plugs into the Pregel runtime
- [Memory System](memory.md) — the four-level memory architecture and how L2 compression relates
- [Agents and Execution Modes](agents.md) — how the pipeline applies in `chat`, `three_layer`, and `workflow` modes
- [Knowledge & Memory Design](../design/knowledge-memory-design.md) — the full memory system design
