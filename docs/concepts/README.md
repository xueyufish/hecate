# Concepts

Explanatory articles that help you understand Hecate's core ideas before building agents, configuring workflows, or operating a deployment.

If you prefer a hands-on introduction, start with the [Quickstart](../getting-started/quickstart.md) and the [tutorials](../tutorials/). For deep technical detail, see the [design documents](../design/).

## Articles

- **[Overview](overview.md)** — what Hecate is, how it is structured, and how the pieces fit together. Read this first.
- **[Agents and Execution Modes](agents.md)** — the three execution modes (`chat`, `three_layer`, `workflow`), what runs when a chat request arrives, and how to choose between them.
- **[The Execution Engine](engine.md)** — the Pregel/BSP runtime in four core ideas: graphs, channels, supersteps, and checkpoints.
- **[Context Engineering](context-engineering.md)** — the pipeline that assembles the right context for every LLM call: token budgets, message prioritization, evidence tracking, and offloading.
- **[Memory System](memory.md)** — the four-level memory architecture (working, episodic, semantic, procedural) and what your agent remembers across turns, sessions, and users.
- **[Guardrails and Hooks](guardrails.md)** — the four engine-level hook types (Pre/Post LLM/Tool) that enforce PII masking, injection defense, and audit logging at every trust boundary.
- **[Multi-Tenancy](multi-tenancy.md)** — the Organization → Workspace → User hierarchy, data isolation via `workspace_id`, and what it means for agent configuration and user management.
