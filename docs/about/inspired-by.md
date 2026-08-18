# Inspired by

Hecate builds on the shoulders of many proven projects and open standards. This page credits each one and explains what Hecate borrowed — and what it deliberately chose not to.

The scope includes 17 projects from our research plus the core infrastructure that powers Hecate. For each entry we note **what Hecate borrowed** and **where it differs** (positive or negative). This is the honest "who we learned from" page; treat it as required reading before evaluating Hecate against alternatives.

---

## Execution model

### Core inspiration

- **[Google Pregel](https://research.google/pubs/pub37252/)** — the original Bulk Synchronous Parallel (BSP) graph computation paper. Hecate's superstep loop is named after and directly inspired by Pregel. We use the same conceptual model: vertices compute, barrier, message passing, repeat.
- **[LangGraph](https://github.com/langchain-ai/langgraph)** — pioneered channel/checkpoint/Pregel patterns in the Python agent ecosystem. Hecate borrows the conceptual model (channel types, checkpoint persistence, interrupt/resume) but **re-implements the runtime from scratch** with zero external framework dependencies. The rationale is in [ADR-001](../design/adr/001-graph-first-orchestration.md).
- **[Apache Beam](https://beam.apache.org/)** — the dataflow model that influenced how we think about bounded parallelism, watermarks, and event-time processing. Hecate's WorkerPool and asynchronous execution borrow from Beam's execution model.

### Related frameworks

- **[LangChain](https://github.com/langchain-ai/langchain)** — the broader Python agent framework that LangGraph is built on. We borrow little from LangChain directly (Hecate is intentionally a smaller, more focused project), but we admire their abstractions for model + tool integration.
- **[CrewAI](https://www.crewai.com/)** — pioneered the "crew of agents" mental model with role + task + delegation. Hecate's multi-agent orchestration patterns (Hierarchical, Handoff, Pipeline, Broadcast, Negotiation, Debate) are inspired by CrewAI's teams-framework but expressed as graph DSL, not Python classes.
- **[AutoGen](https://github.com/microsoft/autogen)** — Microsoft's multi-agent framework. We borrow the pattern of "executing agents collaborate via message passing" but reject AutoGen's dynamic code execution as a default (we require explicit configuration).

---

## Protocols

### Model Context Protocol (MCP)

**[Anthropic's Model Context Protocol](https://modelcontextprotocol.io/)** is the open standard for agent-to-tool integration. Hecate ships:

- **MCP client** — consume external tools (GitHub, Slack, internal services) via MCP servers
- **MCP server** — expose Hecate agents and tools as MCP primitives so Claude Desktop, Cursor, or any MCP client can invoke them

Streamable HTTP transport. Bidirectional — Hecate is both client and server. See [How-to: Enable MCP Server](../how-to/enable-mcp-server.md).

### Agent-to-Agent (A2A) Protocol

**[Linux Foundation A2A v1.0 GA](https://a2a-protocol.org/)** is the open standard for cross-framework agent communication. Hecate implements:

- **AgentCard** at `/.well-known/agent-card.json` for discovery
- **JSON-RPC 2.0** task lifecycle at `/a2a/`
- **SSE streaming** for long-running tasks
- **Signed AgentCards** (JWS + RFC 8785) for trust

Hecate operates as both A2A server and A2A client. See [A2A Protocol concept](../concepts/a2a-protocol.md) and [A2A Architecture](../design/a2a-architecture.md).

### OpenAI Function Calling

The **OpenAI Function Calling / Tools API** spec defines the wire format Hecate uses for tool-use. Specifically:

- `tools` array in chat completions request (JSON Schema)
- `tool_calls` array in response
- Streaming tool calls via `delta.tool_calls`

Hecate exposes this format at `/v1/chat/completions` (see [OpenAI Compatibility tutorial](../tutorials/10-openai-compatibility.md)). This is why any OpenAI-compatible client (litellm, langchain-openai, instructor, vllm) works against Hecate with no code changes.

---

## Visual / UI

- **[React Flow](https://reactflow.dev/)** — the open-source graph visualization library powering Hecate's visual canvas (`web/`). MIT-licensed, actively maintained, with custom node types and MiniMap/Controls out of the box. See [Visual Canvas Architecture](../design/visual-canvas-architecture.md) for the integration details.
- **[Next.js](https://nextjs.org/)** — the React framework for the canvas UI. Picked over Remix for its maturity and ecosystem.
- **[Tailwind CSS](https://tailwindcss.com/)** — utility-first CSS for the canvas UI.

---

## Multi-agent orchestration

- **[LangGraph](https://github.com/langchain-ai/langgraph)** — the six collaboration patterns (Hierarchical / Handoff / Pipeline / Broadcast / Negotiation / Debate) are Hecate's implementation of multi-agent topology. See [Tutorial: Multi-Agent Orchestration](../tutorials/04-multi-agent.md).
- **[CrewAI](https://www.crewai.com/)** — the role-playing-as-task mental model. Hecate represents the same idea but as a graph DSL, not Python classes — for explicitness and engine-level visibility.
- **[AutoGen](https://github.com/microsoft/autogen)** — GroupChat pattern. Hecate's Broadcast pattern is a graphical equivalent.

---

## Monitoring and observability

- **[OpenTelemetry](https://opentelemetry.io/)** — the standard for distributed tracing. Hecate's span bridge (`src/hecate/services/observability/span_processor.py`) converts OTel spans to Hecate's internal `TraceModel` for unified query.
- **[Prometheus](https://prometheus.io/)** — the metrics exposition format. Hecate exposes ~30 standard metrics in Prometheus format.
- **[LangSmith](https://www.langchain.com/langsmith)** — the LLM-observability platform. Hecate ships a `LangFuseTraceProvider` (LangSmith's sibling project) so you can forward spans to any LLM-observability backend.

---

## Enterprise and cloud platforms

These platforms inform Hecate's **positioning** rather than its **code**. We built Hecate specifically to be the **self-hosted alternative** to these managed services.

- **[Salesforce Agentforce](https://www.salesforce.com/agentforce/)** — informed the use-case decomposition in our docs (Service Agent, SDR, Buyer Agent, etc.) and the "named agent" mental model. Hecate targets engineering teams that want to build their own agents, not buy pre-built ones.
- **[AWS Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/)** — the "secure at scale" production runtime positioning. Hecate provides similar guarantees (multi-tenant, RBAC, observability) but self-hosted.
- **[IBM watsonx](https://www.ibm.com/watsonx)** — the "trust + governance" framing. Hecate's audit pipeline and [Threat Model](../design/threat-model.md) draw from watsonx's compliance-first approach.
- **[Palantir AIP](https://www.palantir.com/platforms/aip/)** — the "ontology + agents" combination. Hecate's ontology support ([ADR-014](../design/adr/014-ontology-action-system.md), [ADR-015](../design/adr/015-ontology-augmented-generation.md)) is more limited (1.x) but follows the same philosophy.
- **[Google Gemini Enterprise](https://cloud.google.com/products/gemini)** — the "any model, any deployment" positioning. Hecate's model-agnostic routing comes from the same observation: enterprises want flexibility across providers.

### Where Hecate differs

We deliberately chose **not** to be like these platforms:

- **No managed cloud tier** — Hecate is self-hosted only. You don't get billed per agent or per token.
- **No SaaS dashboard** — the visual canvas is open-source (`web/`), not a hosted product.
- **No vendor lock-in** — replace underlying Postgres / Qdrant / MinIO anytime.

---

## Chinese ecosystem

Hecate is developed independently of any major Chinese cloud vendor, but we learned from the Chinese agent ecosystem:

- **[openjiuwen (华为)](https://openjiuwen.com/)** — pioneered the "Agent Swarm" + "Coordination Engineering" pattern for multi-agent collaboration. Hecate's multi-agent orchestration borrows the spirit (collaborative not authoritative) but implements it as graph DSL, not framework.
- **[AgentScope (阿里 / DAMO)](https://github.com/agentscope-ai/agentscope)** — clean Python SDK design, "toolkit / memory / evaluator" architecture. Hecate's plugin structure mirrors AgentScope's mental model.
- **[智果 AgentArts (华为云)](https://www.huaweicloud.com/product/agentarts.html)** — Skill marketplace + application templates pattern. Hecate has plugin registry but not a marketplace (post-1.0).
- **[Meituan CatPaw](https://catpaw.meituan.com/)** — "Workspace + recording-as-skill" model. Hecate's multi-tenant workspace is similar; Hecate does not (yet) have recording-as-skill.
- **[TorchV](https://www.torchv.com/)** — Chinese enterprise focus, "white-box traceable" positioning. Hecate's audit + observability pipeline serves the same need.

---

## Workflow automation

- **[n8n](https://n8n.io/)** — the general-purpose workflow automation platform. Hecate **deliberately does not compete** with n8n — Hecate is agent-first, n8n is workflow-first. They can be combined: n8n orchestrates cross-app data flows, Hecate handles the agent reasoning inside.
- **[Apache Airflow](https://airflow.apache.org/)** — DAG-based workflow execution. Inspired Hecate's DAG compilation pipeline but specialized for agents.

---

## AI coding assistants

Hecate is **built using** AI coding assistants and is **compatible with** them:

- **[Claude Code](https://www.claude.com/product/claude-code)** — primary coding assistant used by Hecate contributors. The `AGENTS.md` convention in Hecate's repo is specifically designed for Claude Code.
- **[OpenAI Codex](https://openai.com/index/openai-codex/)** — compatible with the Hecate codebase via the same `AGENTS.md` convention.
- **[Hermes Agent](https://hermes-agent.org/)** — interesting for its "self-improving" feedback loop. Hecate's evaluation harness ([Tutorial 08](../tutorials/08-agent-evaluation.md)) is Hecate's take on continuous quality improvement.
- **[Meituan CatPaw](https://catpaw.meituan.com/)** — AI IDE for Chinese developers. Compatible with Hecate's `AGENTS.md` convention.

---

## Infrastructure

### Core

- **[FastAPI](https://fastapi.tiangolo.com/)** — the Python async web framework. Hecate's API layer follows its conventions: Pydantic v2 schemas, dependency injection, automatic OpenAPI generation, async request handling.
- **[Pydantic](https://docs.pydantic.dev/)** — data validation and serialization (v2). Powers every schema, model, and API contract in Hecate.
- **[SQLAlchemy](https://www.sqlalchemy.org/)** — async ORM (2.0). Backs all 69 ORM tables with `workspace_id`-based tenant isolation across 38 tenant-scoped models (see [Multi-Tenancy Architecture](../design/multi-tenancy-architecture.md)).
- **[LiteLLM](https://github.com/BerriAI/litellm)** — unified LLM provider interface. Powers Hecate's model-agnostic routing across 100+ providers (OpenAI, Anthropic, DeepSeek, Qwen, GLM, Ollama, etc.).
- **[TimescaleDB](https://www.timescale.com/)** — time-series database for high-cardinality metrics. Used by the `TimescaleMetricsStore` for production metrics aggregation.
- **[Qdrant](https://qdrant.tech/)** — vector database for embeddings. Default vector store backend; supports the hybrid dense+sparse search that Hecate's RAG uses.
- **[MinIO](https://min.io/)** — S3-compatible object storage. Default for backups, document uploads, and audit log archival.

### Optional / partner

- **[Temporal](https://temporal.io/)** — durable workflow execution. Hecate's built-in checkpointing covers the common case; Temporal is an optional integration for distributed workflows spanning days/weeks.
- **[Redis](https://redis.io/)** — in-memory state store. Hecate's session state store can use Redis for multi-instance deployments.
- **[PostgreSQL](https://www.postgresql.org/)** — the primary OLTP database. Hecate supports PG 14, 15, 16, 17. The `pgvector` extension is an option for small-production RAG deployments.

---

## Process

Hecate's OpenSpec-driven development workflow is inspired by:

- **[Python PEPs](https://peps.python.org/)** — Python Enhancement Proposals
- **[Kubernetes KEPs](https://github.com/kubernetes/enhancements)** — Kubernetes Enhancement Proposals
- **[Rust RFCs](https://github.com/rust-lang/rfcs)** — Rust's RFC process

Like these projects, Hecate tracks every feature through a structured proposal → design → specs → implementation → archive lifecycle. These process documents live in `openspec/` and are contributor-facing, not user-facing.

---

## What we deliberately did NOT borrow

Some platforms have features that look attractive but Hecate intentionally does not implement:

| Feature | Source | Why we skip |
|---|---|---|
| **No-code mobile app for agent building** | Dify, n8n | Mobile-first UX dilutes focus on engine + protocols |
| **Custom model training** | LangChain, HuggingFace | Hecate is an inference platform; training is upstream |
| **Built-in LLM provider** | (proprietary) | Always proxy to upstream; Hecate is not a model lab |
| **Realtime voice / video agents** | (some platforms) | Possible in the future (P5+) but not a near-term focus |
| **Multi-cloud failover** | (enterprise platforms) | Deploy in your cloud of choice; manage failover at infra layer |
| **End-user chatbot widget** | Dify, Salesforce | The OpenAI-compatible API is the widget's backend; anyone can build the UI |

This is documented in detail in [Positioning & Competitive Landscape](../design/positioning.md#what-this-document-is-not).

---

## Credits

Specific acknowledgments (with no implied endorsement):

- The **LangGraph** team for proving the channel + checkpoint + Pregel pattern in Python
- The **A2A Protocol** Linux Foundation working group for the cross-agent standard
- The **MCP** working group at Anthropic for the agent-tool protocol
- The **OpenAI** team for the function-calling spec that Hecate implements
- The **Pydantic** team for the data validation library without which Hecate would be a mess
- The **Hecate contributors** who have filed issues, opened PRs, and contributed docs

---

## Related documents

- [Positioning & Competitive Landscape](../design/positioning.md) — what Hecate is and isn't
-  — where Hecate is going
- [Architecture Decision Records](../design/adr/) — the 28 design decisions
- [Contributing Guide](../../CONTRIBUTING.md) — how to contribute code
- [License](../../LICENSE) — MIT