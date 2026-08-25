# FAQ

Answers to the questions that come up most often when getting started with Hecate. Grouped by topic; each answer links to the relevant deep dive.

For canonical entity definitions, see [Core Concepts](../design/concepts.md). For term definitions, see the [Glossary](glossary.md).

---

## Getting started

### Which execution mode should I choose — `chat`, `three_layer`, or `workflow`?

- **`chat`** — single LLM call per message, with tools and knowledge available. Best for direct Q&A and single-task automation. This is the default and what the [first tutorial](../tutorials/01-first-agent.md) uses.
- **`three_layer`** — a preset Guard → Plan → Execute pipeline with built-in safety checks and planning. Use it when a single call isn't enough but you don't need a custom graph.
- **`workflow`** — a custom graph executed by the Pregel runtime. Use it for multi-agent coordination, conditional branching, and any custom topology. See the [multi-agent tutorial](../tutorials/04-multi-agent.md).

All three share the same engine, checkpoint system, and guardrails. Moving up the chain is an increase in structure, not a platform change. See [Agents and Execution Modes](../concepts/agents.md).

### Do I need all four Docker services (PostgreSQL, Qdrant, MinIO, Temporal)?

**PostgreSQL is required** — it holds agents, sessions, checkpoints, and all configuration. **Qdrant and MinIO are required for the RAG/knowledge-base features** (vector search and document storage). **Temporal is optional** — it powers durable distributed execution and is not needed for the core runtime. See [Quickstart](../getting-started/quickstart.md) Step 2.

### Can I use a local model like Ollama?

Yes. Hecate routes all LLM traffic through [LiteLLM](https://github.com/BerriAI/litellm), so any provider works — including local models via Ollama. No API key is needed for Ollama; Hecate detects the `ollama/` model prefix and routes to `localhost:11434`. See the provider table in [Quickstart](../getting-started/quickstart.md#using-other-llm-providers).

### Do I need the visual canvas, or can I use code only?

Either. The visual canvas (Agent Studio) and the Python SDK both emit the **same JSON graph DSL**, and both feed the same compiler. Neither is a wrapper around the other. You can build everything from code and never open the canvas. See [ADR-010](../design/adr/010-react-flow-canvas.md).

---

## Execution engine

### What is a superstep?

One iteration of the Pregel execution loop: ready nodes read their input channels, the worker pool dispatches them, the runtime awaits all results, applies writes, appends a `STEP_END` commit event to the event log, and evaluates conditional edges to decide what runs next. Execution ends when no more nodes are ready. See [The Execution Engine](../concepts/engine.md#supersteps-the-execution-loop).

### What is the difference between a checkpoint and a session?

A **session** is one conversation lifecycle (`active` → `interrupted` → `active` → `completed`/`failed`). Execution state is **event-sourced** (Log-as-Truth, [ADR-030](../design/adr/030-event-sourced-execution-state.md)): every superstep appends channel-write events and a `STEP_END` commit to the event log, and a **checkpoint** is a discardable materialized cache of that log (channel state + `log_version` cursor). One session has one event log and many cache materializations. See [The Execution Engine](../concepts/engine.md#event-log-and-checkpoints-durable-resumable-state).

### What happens when a session is interrupted?

When a node calls `interrupt()`, the runtime commits the event log up to the interrupt point and stops. The session enters the `interrupted` state and **holds no resources** — it is a committed log tail waiting to be resumed. When you resume with a `Command`, the runtime derives the pause point from the log (cache + tail replay), injects your input, and continues from exactly where it stopped. See the [Human-in-the-Loop tutorial](../tutorials/06-human-in-the-loop.md).

### Can nodes run in parallel?

Yes. The `fan-out` node type dispatches multiple branches to run concurrently within a single superstep, and the `merge` node collects their results. The worker pool dispatches all ready nodes each superstep. See [Graph DSL Reference](graph-dsl.md) and [The Execution Engine](../concepts/engine.md).

### How do I add a human approval step?

Use `interrupt()` at the point where you want to pause, and resume with a `Command` carrying the approver's decision. For tool-level approval, a `PreToolHook` can call `interrupt()` automatically for `HIGH` or `CRITICAL` risk tools. See [Guardrails and Hooks](../concepts/guardrails.md#risk-levels-and-approval-scopes).

---

## Context and memory

### How does the agent stay coherent in very long conversations?

Two mechanisms cooperate: the **Context Engineering pipeline** runs before every LLM call to keep the prompt within the token budget (token budget manager, message prioritizer, tool filter), and **L2 conversation compression** progressively summarizes old messages as the window fills (snip → microcompact → autocompact). Together they let a conversation run for hundreds of turns. See [Context Engineering](../concepts/context-engineering.md) and [Memory System](../concepts/memory.md#l2-conversation-memory).

### What is the difference between L2 compression and Context Offloading?

**Compression is lossy** — once messages are summarized, the original content is gone. **Context Offloading is lossless** — dropped messages are written to the `AgentEnvironment` filesystem as JSON and replaced with a compact reference stub; the agent can retrieve the full content on demand via `read_file`. Offloading runs *before* compression so that recoverable content is preserved whenever possible. See [Context Engineering](../concepts/context-engineering.md#context-offloading-preserve-what-compression-would-lose).

### Will one user's L3 memories leak to another user?

No. L3 (User Memory) is scoped per user + agent + session. Facts remembered for one user are not retrievable when the agent talks to a different user. This makes it safe to deploy one agent serving many users with personalized experiences. See [Memory System](../concepts/memory.md#l3-user-memory).

### Do I configure the context pipeline per agent?

No. The Context Engineering pipeline runs automatically for every agent — you do not tune it per agent. Knowing it exists helps you understand *why* long sessions stay coherent and *why* the same workflow works across providers, but it is not a per-agent configuration surface. See [Context Engineering](../concepts/context-engineering.md#why-this-matters-for-your-agents).

---

## Security and guardrails

### Can a guardrail hook be bypassed?

No — not through normal execution. The four hooks (`PreLLMHook`, `PostLLMHook`, `PreToolHook`, `PostToolHook`) live inside the engine's execution loop, not in an LLM-client wrapper or HTTP proxy. Every LLM invocation and every tool execution — regardless of which node triggered it or whether it came from a sub-agent — passes through the same four hooks. There is no alternate code path. See [Guardrails and Hooks](../concepts/guardrails.md#why-engine-level-not-wrapper-level).

### How does PII masking work, and is it reversible in the response?

`InputSecurityHook` (a `PreLLMHook`) scans the prompt and replaces sensitive patterns (SSN, credit card, email, phone, passport) with opaque tokens before the LLM sees them. After the LLM responds, `OutputSecurityHook` (a `PostLLMHook`) substitutes the tokens back so the end user sees the real values. The LLM itself never sees the raw PII. See [Guardrails and Hooks](../concepts/guardrails.md) and the [Guardrails tutorial](../tutorials/05-guardrails-hooks.md).

### What is the difference between risk level and approval scope?

- **Risk level** (`LOW` / `MEDIUM` / `HIGH` / `CRITICAL`) classifies the potential blast radius of an operation — it is a property of both Tools and Agents.
- **Approval scope** (`once` / `session` / `project` / `global`) defines how long an approval stays valid.

A `LOW`-risk tool with `session` approval runs automatically for the whole session; a `CRITICAL`-risk tool with `once` approval pauses for human sign-off every single time. See [Guardrails and Hooks](../concepts/guardrails.md#risk-levels-and-approval-scopes).

### Where do security events go?

Every hook execution produces a `SecurityEvent` that flows through a SIEM pipeline, decoupled from synchronous hook latency. Events can be exported to webhooks (Slack, PagerDuty), syslog (RFC 5424), or OCSF format. Long-lived `SecurityFinding` records from the FindingEngine are queryable via `GET /api/security/findings`. See [Guardrails and Hooks](../concepts/guardrails.md#from-hook-events-to-the-siem-pipeline).

---

## Multi-tenancy

### How is data isolated between workspaces?

At the database layer. Every tenant-scoped data model (35 of them) carries a `workspace_id` foreign key, and the API enforces that a user in Workspace A cannot query or invoke Workspace B's resources. This is data-level isolation, not just UI-level hiding. See [Multi-Tenancy](../concepts/multi-tenancy.md).

### Can a user belong to multiple workspaces?

Yes. Workspace membership is managed separately from the Organization-level role (`admin` / `editor` / `viewer`). A user may be a member of multiple workspaces with different access in each. See [Multi-Tenancy](../concepts/multi-tenancy.md#user).

### What is the difference between an Organization and a Workspace?

An **Organization** is the top-level tenant boundary (one per customer in SaaS). A **Workspace** is the unit of isolation inside an Organization — all agents, workflows, knowledge bases, tools, and prompts belong to a workspace. A common pattern is one workspace per team, project, or product line. See [Multi-Tenancy](../concepts/multi-tenancy.md#the-three-levels).

---

## Protocols

### What is the difference between MCP and A2A?

- **MCP (Model Context Protocol)** connects an **agent to tools** — Hecate is both an MCP client (consumes external tool servers) and an MCP server (exposes Hecate's own tools/knowledge to clients like Claude Desktop or Cursor).
- **A2A (Agent-to-Agent Protocol)** connects **agents to other agents** across frameworks — Hecate agents are discoverable via Agent Cards and invokable by LangGraph, CrewAI, AutoGen, or custom agents.

See [Enable MCP Server](../how-to/enable-mcp-server.md) and [Enable A2A Server](../how-to/enable-a2a-server.md).

### Can Hecate be both an MCP client and an MCP server at once?

Yes. The MCP client lets your agents call external MCP tool servers; the MCP server exposes Hecate's agents, knowledge bases, and tools as MCP primitives that other clients can invoke. Both can run simultaneously. See [Enable MCP Server](../how-to/enable-mcp-server.md).

---

## Deployment

### Can I self-host, and do prompts leave my network?

Yes to both self-hosting and data residency. Hecate is OSS and designed for on-premises and regulated deployments — you run it on your own infrastructure with your own API keys. Prompts and completions go directly to your configured LLM provider; Hecate does not store or forward them to any third party. See the [Trust & Security section of the README](../../README.md#trust--security).

### How do I scale horizontally?

Run multiple Hecate replicas behind a load balancer and move session state to Redis (so any replica can resume any session). The deployment guide covers this pattern. See [Deploy to production — Horizontal scaling](../how-to/deploy-production.md#horizontal-scaling).

### How do I roll back a bad deployment?

There are four rollback paths depending on what went wrong: code revert, database downgrade (Alembic), feature-flag toggle, or blue-green switch. See the [Rollback Runbook](../operations/rollback.md) for the decision tree and exact commands, and [Version and roll back an agent](../how-to/version-and-rollback-agent.md) for agent-level versioning.

---

## Extensibility

### How do I add a custom node type?

Implement the `Worker` extension point — a stateless class that receives a read-only channel snapshot and returns the values to write. Register it with a worker pool. The engine dispatches it like any built-in node. See [Extension Points](extension-points.md#2-worker).

### How do I swap the scheduler or checkpoint store?

Implement the corresponding ABC — `SchedulerStrategy` (`select_next`, `set_weights`) or `CheckpointStore` (`save`, `load`, `list_checkpoints`) — and wire your implementation into the runtime instead of the default (`FIFOScheduler` / `InMemoryCheckpointStore`). Production deployments typically provide a PostgreSQL-backed `CheckpointStore`. See [Extension Points](extension-points.md).

### What is the difference between an extension point and an SPI?

Both are abstract interfaces, but an **extension point** is a full ABC you must implement to customize behavior (many of them: `Worker`, `WorkerPool`, `CheckpointStore`, etc.), while the **multiple SPI methods** on `EnginePort` (`context_assemble`, `evidence_query`, `agent_execute`, `tool_execute_sandbox`, `workflow_execute`, `llm_invoke_structured`) ship with default implementations and are *optional* overrides for service-layer adapters. See [Extension Points](extension-points.md).

---

## Compatibility

### Is the chat API really OpenAI-compatible? Can I drop in existing clients?

Yes. The `/v1/chat/completions` endpoint returns the OpenAI Chat Completions response shape, and authorization uses a bearer API key. Existing OpenAI-compatible clients (SDKs, curl scripts, UIs) work as drop-in replacements — point them at `http://your-hecate-host:8000/v1` with a Hecate API key. See [Quickstart](../getting-started/quickstart.md#step-6--send-your-first-chat-request) Step 6.

### Which LLM providers are supported?

All of them — Hecate routes through LiteLLM, which covers 100+ providers including OpenAI, Anthropic, DeepSeek, Qwen (DashScope), GLM (Zhipu), and local models via Ollama. Set the provider's API key in `.env` and use the corresponding model prefix in your requests. See the provider table in [Quickstart](../getting-started/quickstart.md#using-other-llm-providers) or the [LiteLLM provider docs](https://docs.litellm.ai/docs/providers).

### Are there official SDKs?

Hecate's API surfaces are standard HTTP: the OpenAI-compatible `/v1/` endpoint works with any OpenAI client library, and the management `/api/` surface is a standard REST/JSON API usable from any HTTP client. There is no separate official SDK to install. See the [REST API reference](rest-api.md) for the route map.
