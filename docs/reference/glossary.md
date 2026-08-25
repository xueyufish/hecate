# Glossary

Definitions for the domain terms, acronyms, and Hecate-specific vocabulary used throughout this documentation. Terms are grouped by area; each entry links to the article where the concept is explained in depth.

For the canonical entity definitions (Agent, Workflow, Knowledge Base, Tool, etc.), see [Core Concepts](../design/concepts.md).

---

## Execution Engine

- **Pregel** — Google's Bulk Synchronous Parallel graph-computation model. Hecate's execution engine borrows the superstep loop and channel/checkpoint patterns from it and re-implements the runtime from scratch with no external framework dependencies. See [The Execution Engine](../concepts/engine.md).
- **BSP (Bulk Synchronous Parallel)** — an execution model where parallel workers run between synchronized barriers. In Hecate, each barrier is a superstep boundary where writes are consolidated and a checkpoint is persisted.
- **Superstep** — one iteration of the Pregel loop: ready nodes read channels, the worker pool dispatches them, the runtime awaits all results, applies writes, appends a `STEP_END` commit event to the event log, and evaluates conditional edges. Execution ends when no more nodes are ready.
- **Graph** — a directed structure of nodes and edges; the unit of work in `workflow` mode. Written as JSON conforming to the [Graph DSL](graph-dsl.md); the visual canvas and Python SDK emit the same JSON.
- **Node** — a typed execution unit in a graph. Hecate defines ten node types: `conversation`, `tool-call`, `condition`, `agent`, `knowledge-retrieval`, `variable-set`, `fan-out`, `merge`, `suggestion`, `coordinator`. See [Graph DSL Reference](graph-dsl.md).
- **Edge** — a connection between nodes. Unconditional edges are always taken; **conditional** edges are taken only when their expression evaluates true. Execution begins at the reserved node ID `__start__` and ends at `__end__`.
- **Channel** — a typed state slot managed by the runtime. Nodes read from and write to channels rather than passing arguments directly, which makes state explicit and serializable. Four channel types: `last_value`, `topic`, `accumulator`, and the deprecated `persistent_topic` (auto-migrated to `topic` with `persistent: true`).
- **Worker** — a stateless unit that executes a single node's work (calls an LLM, runs a tool, queries a knowledge base). It receives a read-only channel snapshot and returns the values to write. Workers never directly mutate state. See [Extension Points](extension-points.md#2-worker).
- **WorkerPool** — dispatches ready workers each superstep. The default `DirectWorkerPool` runs in-process. See [Extension Points](extension-points.md#3-workerpool).
- **Checkpoint** — a discardable **materialized cache** of execution state (channel values + `log_version` cursor) rebuilt by folding the event log. Not the source of truth — the event log is (Log-as-Truth, [ADR-030](../design/adr/030-event-sourced-execution-state.md)). Materialized at turn end / interrupt / every N supersteps. See [Extension Points](extension-points.md#4-checkpointstore).
- **Compiler** — validates and transforms a JSON graph definition into a `CompiledGraph` the Pregel loop can execute. Stages: schema validation, dependency analysis, channel binding, optimization (dead-node elimination, parallel-branch detection).
- **EnginePort** — the boundary interface (Ports and Adapters pattern) between the engine and external services (LLM providers, tool runners, knowledge bases, checkpoint and conversation storage, observability). The engine depends on the abstract port; production code supplies the adapter. See [Extension Points](extension-points.md#1-engineport).
- **Extension Point** — an abstract interface (ABC) in the engine layer that lets you customize execution. Hecate defines many engine extension interfaces plus 6 optional SPI methods on `EnginePort`, and multiple plugin SPI types at the platform layer. See [Extension Points Reference](extension-points.md).
- **SPI (Service Provider Interface)** — the 6 optional methods on `EnginePort` (`context_assemble`, `evidence_query`, `agent_execute`, `tool_execute_sandbox`, `workflow_execute`, `llm_invoke_structured`) that ship with default implementations and can be overridden by service-layer adapters.
- **`interrupt()`** — pauses execution at a superstep barrier to wait for external input (typically human approval). The runtime commits the event log up to the interrupt point and stops; resources are released while paused.
- **`Command`** — resumes an interrupted session by injecting the user's input and continuing the superstep loop from the log-derived pause point where it stopped.
- **Session** — one conversation lifecycle: `active` → `interrupted` → `active` → `completed` (or `failed`). An interrupted session holds no resources — it is a committed event-log tail waiting to be resumed.

### Execution modes

- **`chat` mode** — the simplest agent mode: assemble context, run a single LLM call, return the response. Tools and knowledge bases are available; there is no multi-step planning.
- **`three_layer` mode** — a preset Guard → Plan → Execute pipeline with a condition node that loops back to planning when the task is incomplete. Adds structure and safety checks without a custom graph.
- **`workflow` mode** — the most powerful mode: the agent is bound to a custom graph and executed by the Pregel runtime. Supports any topology — pipelines, fan-out, handoff, broadcast, negotiation, debate.

---

## Context Engineering

- **Context Engineering** — the pipeline that assembles the right context for every LLM call, within the model's token budget, with the most relevant material prioritized. Runs before each LLM invocation inside the engine. See [Context Engineering](../concepts/context-engineering.md).
- **Phase Detector** — classifies the current task phase (planning, executing, reflecting, answering) to bias downstream budget allocation.
- **Evidence Tracker** — normalizes verbose tool outputs into concise, citation-friendly structured evidence the LLM can reference.
- **Token Budget Manager** — divides the context window into allocations (system prompt, memory, history, evidence, tools) and enforces a hard ceiling to prevent token-limit errors.
- **Message Prioritizer** — ranks conversation messages by relevance and recency; drops the lowest-ranked when the history allocation exceeds budget. Smarter than FIFO truncation.
- **Tool Filter** — selects the subset of tools relevant to the current phase, reducing tool-choice noise and saving tokens.
- **Provider Shaping** — adapts the assembled context to a specific model's prompt format, system-message conventions, and tool-calling schema, so one workflow runs across providers.
- **Context Assembler** — the final pipeline stage that combines filtered, prioritized, budgeted context into the actual prompt sent to the LLM.
- **Context Offloading** — writes messages dropped for budget reasons to the `AgentEnvironment` filesystem as JSON and inserts a compact reference stub in the live context, so the agent can retrieve the full content on demand via `read_file` instead of losing it to lossy compression.
- **Reference stub** — the compact placeholder (topic summary + file path) that replaces offloaded messages in the live context.
- **AgentEnvironment** — the per-agent filesystem where offloaded context and other working files are stored.

---

## Memory System

Hecate's four-level memory architecture, inspired by cognitive-science models. Each level persists state differently and serves a different timescale. See [Memory System](../concepts/memory.md).

- **L1 — Working Memory** — named blocks in the agent's context window (`persona`, `user_profile`, `current_task`, etc.) that the agent reads and edits during a single execution. Ephemeral; gone when the session ends unless checkpointed or promoted to L3.
- **L2 — Conversation Memory** — the conversation history within a single session, kept within budget by a progressive compression pipeline (snip → microcompact → autocompact). Checkpointed so interrupted sessions resume intact.
- **L3 — User Memory** — cross-session, per-user facts (preferences, ongoing projects, established context) extracted automatically via a Mem0-style approach: extraction → embedding → multi-signal-fusion retrieval. Scoped per user so facts never leak across users.
- **L4 — Knowledge Memory** — workspace-wide structured knowledge from documents, accessed via RAG retrieval. Backed by the Knowledge Base system (Docling parsing → chunking → BGE-M3 embedding → Qdrant hybrid index).

---

## Security and Guardrails

- **Guardrail / Hook** — an engine-level interception at one of four trust boundaries. Because hooks live in the engine — not in an LLM-client wrapper or HTTP proxy — every execution path passes through them. See [Guardrails and Hooks](../concepts/guardrails.md).
- **Middleware chain (E3)** — since guardrail-upgrade-trio, each hook position hosts an ordered chain of stages (`AGENT_REQUEST` / `LLM_RESPONSE` / `TOOL_PRE_EXECUTE` / `TOOL_RESULT` phases). `BLOCK` short-circuits with the originating stage's identity; `SANITIZE` flows modified data downstream; downstream blocks are monotonic (cannot be healed). The legacy hook ABCs adapt as single stages.
- **PreLLMHook** — fires before messages are sent to the LLM (chain phase `AGENT_REQUEST`). Used for PII masking, prompt-injection detection, and token-budget enforcement.
- **PostLLMHook** — fires after the LLM returns (chain phase `LLM_RESPONSE`). Used for content filtering, response redaction, and toxic-output blocking.
- **PreToolHook** — fires before a tool is invoked (chain phase `TOOL_PRE_EXECUTE`). Used for permission checks, risk-level gating, and argument validation.
- **PostToolHook** — fires after a tool returns (chain phase `TOOL_RESULT`). Used for result auditing, sensitive-output redaction, and side-effect logging.
- **SecurityHookSet** — a `namedtuple` bundling the four hooks, assembled by `create_security_hooks(guardrail_config)` from per-agent configuration; production bundles come from `assemble_guardrails` which adds chains, policy rules, and the approval callback.
- **Fail-closed approval** — a `REQUIRE_APPROVAL` decision with no configured answerer denies the call and still emits the full `APPROVAL_ASKED`/`APPROVAL_DECIDED` audit pair to the event log (turn-enclosed). ONCE grants are consumed on first use.
- **Monotonic denial** — a denied `tool_call_id` stays denied for the session (runtime tracker); the `MONOTONIC.DENIAL` log invariant fail-stops if a denied call is later executed. Resurrection is a bug.
- **Risk level** — a Tool and Agent security attribute: `LOW`, `MEDIUM`, `HIGH`, or `CRITICAL`. Classifies the potential blast radius of an operation.
- **Approval scope** — how long an approval remains valid: `once`, `session`, `project`, or `global`.
- **LLM Guard** — Hecate's content-filtering layer: `PreLLMHook` blocks prompt-injection attempts; `PostLLMHook` blocks toxic or policy-violating outputs.
- **PII (Personally Identifiable Information)** — sensitive patterns (SSN, credit card, email, phone, passport) detected and masked by `InputSecurityHook` before the LLM sees them, then restored by `OutputSecurityHook` in the final response.
- **SIEM pipeline** — Security Information and Event Management pipeline that ingests hook events asynchronously (decoupled from synchronous hook latency) and exports them to webhook, syslog, or OCSF destinations.
- **SecurityEvent** — the normalized event record written by hooks (action, agent, session, decision, severity, timestamp).
- **ToolDecision** — the structured outcome of a tool access check (`allow` / `deny` / `require_approval`), persisted to PostgreSQL via `ToolDecisionModel`.
- **SecurityFinding** — a long-lived finding produced by the FindingEngine when a hook detects a policy violation; queryable via `GET /api/security/findings`.
- **AuditSink** — a pluggable destination for audit events with batched async writes and retention cleanup.
- **OCSF (Open Cybersecurity Schema Framework)** — one of the export formats supported by the SIEM pipeline.

---

## Protocols and Integration

- **MCP (Model Context Protocol)** — Anthropic's open protocol for agent-to-tool integration. Hecate ships a native MCP **client** (consume external tools) and MCP **server** (expose Hecate as a tool provider) using Streamable HTTP transport. See [Enable MCP Server](../how-to/enable-mcp-server.md).
- **A2A (Agent-to-Agent Protocol)** — the Linux Foundation standard standard for cross-framework agent communication. Hecate agents are A2A-discoverable via Agent Cards and invokable via the A2A task lifecycle. See [Enable A2A Server](../how-to/enable-a2a-server.md).
- **Agent Card** — the `/.well-known/agent.json` discovery document that exposes an agent's capabilities to A2A clients.
- **Streamable HTTP** — the MCP transport Hecate uses (upgraded from the older SSE transport per [ADR-012](../design/adr/012-mcp-streamable-http.md)).
- **RAG (Retrieval-Augmented Generation)** — retrieving relevant document chunks at query time and injecting them into the LLM context, rather than fitting all knowledge into the prompt. Powers L4 Knowledge Memory.
- **HITL (Human-in-the-Loop)** — pausing agent execution for human input at a superstep boundary via `interrupt()`, then resuming with a `Command`. See [Human-in-the-Loop tutorial](../tutorials/06-human-in-the-loop.md).
- **LiteLLM** — the unified LLM-provider interface library that powers Hecate's model-agnostic routing across 100+ providers (OpenAI, Anthropic, DeepSeek, Qwen, GLM, Ollama, and more).
- **NL2X (Natural Language to X)** — the Agent Studio feature that generates a workflow graph or agent configuration from a natural-language description.

---

## Multi-Tenancy

- **Organization** — the top-level tenant boundary. Owns Users and Workspaces. In SaaS, one per customer; in self-hosted deployments, often the company itself. See [Multi-Tenancy](../concepts/multi-tenancy.md).
- **Workspace** — the unit of isolation. All business content (Agents, Workflows, Knowledge Bases, Tools, Skills, Prompts) belongs to a Workspace. Users in Workspace A cannot see or invoke Workspace B's resources through the normal API.
- **`workspace_id`** — the foreign key on 35 tenant-scoped data models that enforces data-level isolation at the database layer.
- **Roles** — Organization-level capabilities: `admin` (manage workspaces and users), `editor` (create and modify resources), `viewer` (read-only).
- **RBAC (Role-Based Access Control)** — the authorization model governing what users can do across Workspaces.
- **SCIM (System for Cross-domain Identity Management)** — the v2 standard Hecate supports for automated user and group provisioning from an IdP. See [Configure SSO and SCIM](../how-to/configure-sso-scim.md).
- **SSO (Single Sign-On)** — federated sign-in via OIDC, SAML, or LDAP, wired into Hecate's identity layer.

---

## Agent Studio

- **Agent Studio** — Hecate's visual development environment: a React Flow canvas, an agent configurator, multi-agent orchestration tools, NL2X workflow generation, and testing tools. See [Agent Studio Design](../design/agent-studio-design.md).
- **Canvas** — the visual graph editor. The canvas emits the same JSON graph DSL the Python SDK produces; both feed the same compiler. See [ADR-010](../design/adr/010-react-flow-canvas.md).

---

## Knowledge Representation

- **Ontology** — a structured representation of domain knowledge (entities, relations, actions). See [ADR-014 Ontology Action System](../design/adr/014-ontology-action-system.md).
- **OAG (Ontology-Augmented Generation)** — augmenting LLM generation with ontology-grounded structure. See [ADR-015](../design/adr/015-ontology-augmented-generation.md).
- **GraphRAG / DRIFT** — planned enhancements to the RAG pipeline that combine graph-based retrieval with drift-style multi-hop reasoning. See [RAG Pipeline Design](../design/rag-pipeline-design.md).

---

## Data Loss Prevention (DLP)

- **DLP (Data Loss Prevention)** — Hecate's unified outbound detection engine. Scans content at every trust boundary for sensitive data and applies a configurable per-entity policy. See [DLP](../concepts/dlp.md).
- **DLPScanner** — the three-layer orchestrator (Detection → Policy → Enforcement) shared by all five trust boundaries. Wired into the app lifespan as `app.state.dlp_scanner`.
- **Recognizer** — a detector for one category of sensitive data. Four ship: `RegexRecognizer` (PII patterns, always), `SecretsRecognizer` (detect-secrets wrapper), `PresidioRecognizer` (optional ML NER via spaCy), `DictionaryRecognizer` (custom term lists).
- **DLPPolicyResolver** — resolves the action for a detected entity via a three-level scope override (org → workspace → agent; most specific wins).
- **`is_locked`** — a policy flag that prevents lower scopes from relaxing a rule. Used for security red lines (e.g. secrets → BLOCK is locked by default).
- **DLP Action** — one of `ALLOW`, `MASK`, `BLOCK`, `AUDIT`. Strictest wins on conflict. `AUDIT` = detect and log without altering, for safe rollouts.
- **EgressFilter** — an abstract filter chain injected into `HecateMCPClient.call_tool()`; `DLPEgressFilter` is the DLP implementation that scans MCP responses before they enter agent context (the fifth trust boundary).
- **StreamingDLPWrapper** — incremental scanner for streamed LLM output: 300-char buffer + 10-char overlap, with a final full-scan backstop.
- **SecurityFinding** — the shared finding record DLP writes (reused `SecurityFindingModel`, `rule_name` prefixed `dlp:`). Accepts true/false-positive feedback via `POST /api/security/findings/{id}/feedback`.

---

## Agent evaluation

- **Evaluation Dataset** — a named, workspace-scoped collection of test cases (items) used to grade an agent. See [Agent Evaluation](../concepts/evaluation.md).
- **Evaluation Item** — a single test case in a dataset: the input plus reference material (golden answer, expected tool calls) the evaluators grade against.
- **Evaluator** — a named metric that grades an agent's answers. Hecate ships nine built-in: five agent-quality evaluators (`correctness`, `relevancy`, `completeness`, `tool_call_accuracy`, `task_completion`) and four RAG evaluators (`context_precision`, `context_recall`, `faithfulness`, `answer_relevancy`, require `ragas`).
- **Evaluation Run** — the act of grading. `POST /api/evaluation/runs` executes immediately over a dataset with the selected evaluators and returns scores in the response.
- **Evaluation Score** — the per-item, per-metric grade: `metric_name`, numeric `value`, `reasoning` (justification), and `source`. Runs aggregate scores into `metric_averages`.
- **AnswerSource** — the `answer_source` field on a run, controlling where the graded answers come from (freshly generated vs. reference) so you can evaluate answers you already have.
- **ragas** — the optional Python package that unlocks the four RAG evaluators; without it, requesting a RAG evaluator returns `INVALID_EVALUATOR`.

---

## General acronyms

| Acronym | Expansion |
|---------|-----------|
| **ABC** | Abstract Base Class |
| **API** | Application Programming Interface |
| **BGE-M3** | BAAI General Embedding v3 (dense + sparse multilingual embedding model used in the RAG pipeline) |
| **BSP** | Bulk Synchronous Parallel (see Execution Engine) |
| **HITL** | Human-in-the-Loop (see Protocols and Integration) |
| **IdP** | Identity Provider |
| **JSON DSL** | JavaScript Object Notation Domain-Specific Language — the graph definition format (see [Graph DSL](graph-dsl.md)) |
| **JWT** | JSON Web Token |
| **LDAP** | Lightweight Directory Access Protocol |
| **MCP** | Model Context Protocol (see Protocols and Integration) |
| **OAG** | Ontology-Augmented Generation (see Knowledge Representation) |
| **OCSF** | Open Cybersecurity Schema Framework |
| **OIDC** | OpenID Connect |
| **PITR** | Point-in-Time Recovery (PostgreSQL backup restore) |
| **PII** | Personally Identifiable Information (see Security) |
| **RBAC** | Role-Based Access Control |
| **RAG** | Retrieval-Augmented Generation (see Protocols and Integration) |
| **ReDoc** | An alternative interactive API-docs UI, served at `/redoc` |
| **SAML** | Security Assertion Markup Language |
| **SCIM** | System for Cross-domain Identity Management (see Multi-Tenancy) |
| **SIEM** | Security Information and Event Management (see Security) |
| **SPI** | Service Provider Interface (see Execution Engine) |
| **SSO** | Single Sign-On (see Multi-Tenancy) |
| **Swagger UI** | Interactive API documentation UI, served at `/docs` |
