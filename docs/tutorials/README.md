# Tutorials

End-to-end, hands-on guides that walk you through building real features with Hecate. Each tutorial is self-contained — pick the one that matches what you want to build.

If you are new, start with the [Quickstart](../getting-started/quickstart.md) first.

---

## Learning path

### Foundational — anyone can follow (~1 hour)

1. **[Build Your First Agent](01-first-agent.md)** *(15 min)* — create a configured agent, bind tools, run multi-turn sessions, master the CLI workflow.
2. **[Knowledge Base and RAG](02-knowledge-base.md)** *(25 min)* — upload documents, configure chunking and embedding, let your agent answer from your own data.
3. **[MCP Tool Integration](03-mcp-integration.md)** *(20 min)* — connect external MCP servers as tool providers, or expose Hecate itself as an MCP server.

### Intermediate — comfortable with Python async + Hecate CLI (~2 hours)

4. **[Multi-Agent Orchestration](04-multi-agent.md)** *(30 min)* — build workflows with six collaboration patterns (Hierarchical, Handoff, Pipeline, Broadcast, Negotiation, Debate); the seventh (`dynamic`, coordinator node) is covered in [ADR-032](../design/adr/032-dynamic-orchestration.md).
5. **[Guardrails and Hooks](05-guardrails-hooks.md)** *(20 min)* — enable built-in PII masking and injection defense, configure shell hooks, write custom Python guardrails.
6. **[Human-in-the-Loop](06-human-in-the-loop.md)** *(15 min)* — add approval checkpoints using `interrupt()` and `Command`, resume sessions from durable pause points.

### Advanced — engine internals awareness helpful (~3 hours)

7. **[Context Engineering](07-context-engineering.md)** *(25 min)* — observe the per-call context pipeline, configure token budgets, enable Context Offloading for long-running agents.
8. **[Evaluate an Agent](08-agent-evaluation.md)** *(20 min)* — build an evaluation dataset, run built-in evaluators, read scores, detect regressions when you change an agent.
9. **[A2A Protocol](09-a2a-protocol.md)** *(25 min)* — connect Hecate to other A2A-compliant agents for cross-framework orchestration; expose Hecate as an A2A server.
10. **[OpenAI SDK Compatibility](10-openai-compatibility.md)** *(20 min)* — use Hecate as a drop-in replacement for OpenAI with the official SDK, litellm, langchain-openai, instructor, and vllm clients.
11. **[Visual Canvas](11-visual-canvas.md)** *(15 min)* — design workflows visually in `web/` and run them as Hecate agents without writing code.

---

## Difficulty legend

| | |
|---|---|
| 🟢 Foundational | Anyone can follow. No Hecate internals needed. |
| 🟡 Intermediate | Comfortable with Python async + the `hecate` CLI. |
| 🔴 Advanced | Useful to understand the Pregel runtime and engine SPI before starting. |

## Tutorial index

| # | Title | Level | Time | Tags |
|---|---|---|---|---|
| 01 | [Build Your First Agent](01-first-agent.md) | 🟢 | 15 min | agent, cli, rest, sessions |
| 02 | [Knowledge Base and RAG](02-knowledge-base.md) | 🟢 | 25 min | rag, vector store, embeddings |
| 03 | [MCP Tool Integration](03-mcp-integration.md) | 🟢 | 20 min | mcp, tools, plugins |
| 04 | [Multi-Agent Orchestration](04-multi-agent.md) | 🟡 | 30 min | workflow, dsl, graph |
| 05 | [Guardrails and Hooks](05-guardrails-hooks.md) | 🟡 | 20 min | security, hooks, pii |
| 06 | [Human-in-the-Loop](06-human-in-the-loop.md) | 🟡 | 15 min | hitl, interrupt, checkpoint |
| 07 | [Context Engineering](07-context-engineering.md) | 🔴 | 25 min | context, tokens, offloading |
| 08 | [Evaluate an Agent](08-agent-evaluation.md) | 🔴 | 20 min | evaluation, datasets, metrics |
| 09 | [A2A Protocol](09-a2a-protocol.md) | 🔴 | 25 min | a2a, interop, signing |
| 10 | [OpenAI SDK Compatibility](10-openai-compatibility.md) | 🟡 | 20 min | openai, sdk, streaming |
| 11 | [Visual Canvas](11-visual-canvas.md) | 🟡 | 15 min | web, canvas, react-flow |

---

## End-to-end use cases

For complete business scenarios that combine multiple features, see [Use Cases](../use-cases/README.md):

- **Customer support bot** — RAG over your docs + guardrails + human escalation
- **Code review agent** — MCP filesystem + multi-agent + evaluation
- **Research team** — multi-agent + context engineering + evaluation

---

## Conventions used in every tutorial

- **API key**: We use `dev-key-change-me` (the default in `.env.example`). Replace with whatever you set in `HECATE_API_KEYS`.
- **Agent IDs**: Real UUIDs like `a1b2c3d4-e5f6-7890-abcd-ef1234567890` — copy from responses.
- **Both REST API and CLI**: When both are available, we show REST first (it makes the data model explicit) then CLI (faster for daily use).
- **Troubleshooting at the end**: Each tutorial ends with a small troubleshooting table for the most common issues.