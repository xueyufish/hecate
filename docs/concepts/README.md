# Concepts

Explanatory articles that help you understand Hecate's core ideas before building agents, configuring workflows, or operating a deployment.

If you prefer a hands-on introduction, start with the [Quickstart](../getting-started/quickstart.md) and the [tutorials](../tutorials/). For deep technical detail, see the [design documents](../design/).

## Articles

- **[Overview](overview.md)** — what Hecate is, how it is structured, and how the pieces fit together. Read this first.
- **[Agents and Execution Modes](agents.md)** — the three execution modes (`chat`, `three_layer`, `workflow`), what runs when a chat request arrives, and how to choose between them.
- **[Workflows](workflows.md)** — the graph DSL in three building blocks (channels, nodes, edges), the ten node types, the compilation pipeline, and the six multi-agent collaboration templates.
- **[The Execution Engine](engine.md)** — the Pregel/BSP runtime in four core ideas: graphs, channels, supersteps, and checkpoints.
- **[Sessions](sessions.md)** — the runtime unit that holds conversation state, enables resume after interruption, and powers time-travel debugging.
- **[Context Engineering](context-engineering.md)** — the pipeline that assembles the right context for every LLM call: token budgets, message prioritization, evidence tracking, and offloading.
- **[Memory System](memory.md)** — the four-level memory architecture (working, episodic, semantic, procedural) and what your agent remembers across turns, sessions, and users.
- **[Knowledge and Retrieval](knowledge-rag.md)** — the RAG pipeline: ingestion (Docling → chunker → BGE-M3 → Qdrant), hybrid retrieval (dense + sparse + RRF), citations, and how it plugs into the engine.
- **[Tools, MCP, and A2A](tools-and-mcp.md)** — the three tool sources (`builtin`/`custom`/`mcp`), the Tool Registry, MCP client/server, A2A agent-to-agent protocol, and the policy pipeline that gates every call.
- **[A2A Protocol](a2a-protocol.md)** — the cross-framework agent-to-agent standard: AgentCard, Task lifecycle, signed AgentCards, and how it differs from MCP.
- **[Plugins](plugins.md)** — the eight plugin types (tool / extension / trigger / model / channel / evaluator / auth / secret), when to use which, and the permissions model.
- **[Skills](skills.md)** — the SkillRegistry's unified abstraction over tools, knowledge bases, workflows, and agents; how it differs from tools and plugins.
- **[Budget & Cost](budget.md)** — token tracking, per-workspace / per-agent budgets, degradation levels, and cost optimization levers.
- **[Webhooks](webhooks.md)** — async event reception (GitHub, Slack, custom), signature verification, retries, dead-letter queues, and webhook-to-workflow binding.
- **[Model Hub](model-hub.md)** — the unified LLM access layer: LiteLLM with 100+ providers, four routing strategies (cost/latency/capability/balanced), fallback chain, per-provider circuit breaker, A/B testing, and gray release.
- **[Guardrails and Hooks](guardrails.md)** — the four engine-level hook types (Pre/Post LLM/Tool) that enforce PII masking, injection defense, and audit logging at every trust boundary.
- **[Observability](observability.md)** — the four signals (traces, metrics, logs, audit), what each captures, where it goes, and how to consume them in production.
- **[Multi-Tenancy](multi-tenancy.md)** — the Organization → Workspace → User hierarchy, data isolation via `workspace_id`, and what it means for agent configuration and user management.
- **[Authentication and Identity](auth-identity.md)** — the two credential types (API Keys for machines, JWTs for humans), SSO (OIDC/SAML/LDAP), SCIM v2 provisioning, and the `AuthProvider` extension point.
- **[Agent Evaluation](evaluation.md)** — the built-in evaluation system: datasets of test cases, nine evaluators (agent + RAG), runs, and scores — the loop that catches regressions before release.
- **[Data Loss Prevention (DLP)](dlp.md)** — the outbound detection engine: recognizers, three-level policy override, four actions, and the five trust boundaries (including MCP responses) it scans.
- **[Command-Line Interface](cli.md)** — the three entry points (`hecate`, `hecate-migrate`, `hecate-flag-audit`), how they differ, and when to use the CLI vs API vs canvas.
