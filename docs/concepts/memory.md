# Memory System

An LLM is stateless — each call sees only the context you send it. "Memory" in an agent system is everything you do to give the model the illusion of continuity: remembering what was said, what was decided, and what the user prefers. Hecate implements a **four-level memory architecture**, inspired by cognitive science models, where each level persists state differently and serves a different timescale.

Understanding the four levels helps you predict what your agent will remember across turns, across sessions, and across users — and how to configure each.

---

## The four levels at a glance

| Level | Name | Scope | Persistence | Analogy |
|-------|------|-------|-------------|---------|
| **L1** | Working Memory | Single execution | Context window (ephemeral) | Short-term memory — what you're actively thinking about |
| **L2** | Conversation Memory | Single session | Compressed in context + checkpointed | Working memory of a conversation |
| **L3** | User Memory | Cross-session, per user | Database (embeddings + metadata) | Long-term memory of a person |
| **L4** | Knowledge Memory | Workspace-wide | Vector store (RAG) | Reference library you can look things up in |

Each level is independent — an agent can use L1 and L2 without L3, or combine all four. They are not layers in a stack; they are different kinds of memory that serve different needs.

---

## L1: Working Memory

**Named blocks in the agent's context window.** An agent can declare labeled regions — `persona`, `user_profile`, `current_task`, `domain_context` — and read or edit their contents during execution. Each block has a position (ordering in the prompt) and a token limit.

Working memory is how an agent carries structured state *within* a single execution. A planning node might write its plan to a `current_plan` block; an execution node reads that block to know what to do. Because the blocks live in the context window, they're visible to the LLM on every call — but they're ephemeral. When the session ends, the working memory is gone unless it was checkpointed or promoted to L3.

**Use it for:** state the agent needs on every turn of the current task — the active plan, the user's stated goal, a running list of constraints.

---

## L2: Conversation Memory

**The conversation history within a single session.** As a conversation grows, the history eventually exceeds the context window. Hecate applies a **progressive compression pipeline** to keep long conversations running without hitting the token limit:

```
Context window filling up
    │
    ├── 1. Snip          — remove the oldest low-value messages
    ├── 2. Microcompact  — summarize small groups of adjacent messages
    └── 3. Autocompact    — generate a running summary of the whole conversation
```

Compression activates in stages as the window fills. The goal is to preserve the most important information (key decisions, user instructions, recent context) while discarding redundancy. This is closely related to the [Context Engineering](context-engineering.md) pipeline — compression is what makes L2 manageable, and the prioritizer is what decides what survives.

A session's compressed conversation state is also captured in checkpoints, so an interrupted session resumes with its conversation memory intact.

**Use it for:** multi-turn conversations that need to run longer than a single context window.

---

## L3: User Memory

**Cross-session, persistent facts about users.** This is where Hecate remembers things across conversations: the user's name, their preferences, facts they've shared, procedures they've established. Without L3, every new session starts from a blank slate.

L3 memory is built using a Mem0-style approach:

1. **Extraction** — as conversations proceed, the system identifies factual statements worth remembering ("I prefer Python over Go", "My team uses PostgreSQL").
2. **Encoding** — each fact is encoded as an embedding and stored with scope (user + agent + session), type (semantic, procedural, episodic), and an importance score.
3. **Retval** — when the agent needs user context, it retrieves relevant memories using multi-signal fusion ranking (semantic similarity + importance + recency).

L3 memory is scoped: facts remembered for one user are not visible when the agent talks to a different user. This makes it safe to deploy an agent that serves many users while still giving each a personalized experience.

**Use it for:** personalization — remembering user preferences, ongoing projects, and established context across sessions.

---

## L4: Knowledge Memory

**Structured knowledge from documents, accessed via RAG retrieval.** This is the [Knowledge Base](../design/concepts.md#knowledge-base) system: you upload documents, Hecate chunks and embeds them, and the agent retrieves relevant chunks at query time.

L4 is not separately labeled as "memory" in the UI — it's accessed through the normal RAG flow. But conceptually it serves the same purpose as human reference memory: the agent doesn't hold it in context continuously, but can look things up when needed.

The RAG pipeline: **Document upload → Docling parsing → Text chunking → BGE-M3 embedding (dense + sparse) → Qdrant hybrid index → retrieval at query time**.

**Use it for:** domain knowledge that's too large to fit in context and doesn't change per conversation — product manuals, internal wikis, policy documents.

---

## How the levels interact

```
┌─────────────────────────────────────────────────────┐
│  L3 User Memory        L4 Knowledge Memory          │
│  (cross-session)       (workspace-wide)             │
│         │                      │                     │
│         └──── retrieved ───────┘                     │
│                    │                                 │
│              ┌─────▼─────┐                           │
│              │ L1 Blocks │ ← written/read per turn   │
│              └─────┬─────┘                           │
│                    │                                 │
│              ┌─────▼─────────────┐                   │
│              │ L2 Conversation   │                   │
│              │ (compressed hist) │                   │
│              └───────────────────┘                   │
└─────────────────────────────────────────────────────┘
```

- **L3 and L4** are retrieved from external stores and injected into the context when relevant.
- **L1** holds structured state the agent actively reads and writes during execution.
- **L2** is the running conversation, compressed as needed to stay within budget.

All four contribute to the context the [Context Engineering](context-engineering.md) pipeline assembles before each LLM call.

---

## Choosing what to use

| You want the agent to... | Use |
|--------------------------|-----|
| Track the current task within a single execution | L1 working memory blocks |
| Hold a coherent long conversation | L2 (automatic — no configuration) |
| Remember user preferences across sessions | L3 user memory (automatic extraction) |
| Answer questions from your documents | L4 knowledge base (upload + bind to agent) |
| Nothing — stateless single-turn Q&A | None required; L2 runs by default but is per-session |

L1 and L2 are part of every session by default. L3 is extracted automatically as conversations proceed. L4 requires you to create a Knowledge Base and bind it to the agent.

---

## Further reading

- [Context Engineering](context-engineering.md) — how memory feeds into the per-call context pipeline
- [Core Concepts: Memory System](../design/concepts.md#memory-system) — the full entity definitions
- [Knowledge & Memory Design](../design/knowledge-memory-design.md) — L2 architecture, RAG pipeline, and the planned Knowledge Graph
- [Knowledge Base and RAG tutorial](../tutorials/02-knowledge-base.md) — hands-on guide to L4
