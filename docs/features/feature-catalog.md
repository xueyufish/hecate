# Hecate Feature Catalog

> **Status**: P1 complete; P2 complete; P3 complete (85/86 physical rows; 8.6-abc 🔀 merged into ChannelBase) — all four close-out items delivered (9.1a + 9.2 / 6.27 / 5.4b); P4 in progress; P5 pending.
> **Purpose**: Map the complete capability domains of the enterprise-grade multi-tenant Agent platform, providing a basis for MVP planning and architecture decisions
> **Priority**: P1 (Usable, months 1-3) → P2 (Enterprise-Ready, months 4-6) → P3 (Trustworthy, months 7-9) → P4 (Intelligent, months 10-12) → P5 (Ecosystem, months 13+)

---

## Statistics

| Priority | Features | Goal | Timeline | Done |
|----------|----------|------|----------|------|
| **P1 Usable** | 19 | Create Agent + Plan-Execute + Configure Model/Tool/Knowledge/Skills + Chat Testing + Self-Hosted Deployment | Months 1-3 | 19/19 (100%) |
| **P2 Enterprise-Ready** | 65 | Canvas + Workflow + Multi-Agent + Memory + Multi-Tenant + RBAC + Basic Evaluation + Basic Security + Basic Observability + Context Engineering + Validation + Scheduled Tasks + Prompt Management + Model Routing + Multi-DB + Multi-Vector-DB + MCP Server + Authentication + Canvas UI Enhancement + Collaboration Patterns + Agent Communication + Routing Rules + Model Playground + Offline Deployment + Webhook Callbacks | Months 4-6 | 65/65 (100%) |
| **P3 Trustworthy** | 86 | Enterprise Foundation (SSO/Quota/Vault/Budget) + Security stack (Execution/Approval/Sandbox/DLP/Environment Security P0) + Observability (Ops Center + Health/Conversation/Tool Analytics + Tracing) + Model Hub (Catalog/Lifecycle/A-B/Gray/Circuit/Encryption) + Multi-Agent (A2A + Signed Cards + Conflict) + Tool Platform (Plugin System + Type Taxonomy + Packaging + **Agent Plugins 1.0 Ingestion (5.5c)** + **Content Scanning (5.13a)** + **T0 Tightening**) + **Dynamic Orchestration (1.3.18)** + **Event-Sourced State (1.3.19)** + **Execution Replay (8.20)** + Multi-Channel Wave 1 (11.2/11.3/11.9-Slack) + Distributed Session State + Backup + Version Upgrade + Platform SPI + Agent Environment family + **MCP Streamable HTTP 2026-07-28 spec (5.4b)**. All four close-out items delivered: 9.1a + 9.2 (change `output-side-typed-findings`, 2026-08-22); 6.27 Browser Automation (change `browser-automation`, 2026-08-22); 5.4b MCP 2026-07-28 Spec Migration (change `mcp-streamable-http`, 2026-08-22). *(2026-08-22 release-scope reclassification: 48 items → P4, 3 channels → P5, 11.8 dropped — see P4 "Deferred from P3" and P5 sections. Count header re-aligned to post-reclassification physical rows, 2026-09-07 P3 audit.)* | Months 7-14 | 85/86 (99%) |
| **P4 Intelligent** | 154 | Self-Learning + Hallucination Detection + Agentic RL + Prompt Self-Optimization + Ontology Actions + OAG + Advanced RAG + GraphRAG + Agentic RAG + Memory Integration + Temporal Memory + Lazy GraphRAG + Intelligent Router + Canvas UI + SCIM + Deterministic Hooks + Skill Auto-Detection + Skill Dependency + 5-Level Intent + Object Logs + Object History + Simulation + Computer-use (6.27a) + DataAgent + VibeCoding + Fine-Grained Permissions + Data Integration + gVisor Sandbox + Kata Containers + Decision Simulation + Multi-Stream Modes + Object CRUD Node + Side-by-side Chat+Canvas + Asynchronous Execution API + Peer Selection + Agent Team Templates + Distributed Team Orchestration + **ACP (2.13)** + **External Policy Engine Interface (9.16)** + **AI Auto-Approval (9.17)** + **Chaos Engineering for Multi-Replica (9.16a)** + **Session-Level microVM Isolation (13.4c)** + **Service Mesh Integration (13.19)** + **Projection Registry (8.21)** + **Atomic File Locks (13.20)** + **Voice Agent Pipeline (11.11)** + **Dual-Format Plugin Convergence (5.5d)** + **48 items deferred from P3 (2026-08-22): Evaluation Suite + Security enhancement + Deployment/Ops + Advanced KB + Canvas nodes + Memory + AIP + Auth + enhancements** + **Model Service Publishing (6.47) + Model Management Quick Wins (6.48) (2026-09-06 AgentArts comparison pull-forward)** + **Intent Package Asset (6.49) (2026-09-06 AgentArts 开发配置 pull-forward)** + **Engine Parity (1.3.21) (2026-09-07 deer-flow/LangGraph engine comparison)** + **Self-Evolution Skill Loop (1.3.6f) (2026-09-15, supersedes the retired 1.3.6/1.3.6a–e skeleton)** | Months 15-18 | 47/154 (31%) |
| **P5 Ecosystem** | 66 | Asset Marketplace + Plugin Security & Signing + Partner Monetization + Agentic Resource Discovery + Industry Capabilities + Compliance + Vision + Desktop + End-User App + PyPI + AgentSpace SDK + EU AI Act + W3C Trace Context + Agent Benchmarks + Edge/Lite + AP2 + Knowledge Graph Viz + Ontology Modeling + Memory Versioning + AI Office + Industrial Data + Asset Operations + Memory Clustering + Self-Planning + Tool Auto-Creation + Firecracker + Cloud Doc Connector + Global Branching + Embedded Ontology + Platform-Level Governance + Zero Trust + **Compliance & Audit Center** + **Model Governance** + **Firecracker microVM Backend (6.40)** + **WASM Runtime Backend (6.41)** + **20 items deferred from P3/P4** (17 from 2026-08-14 re-scope + 11.4/11.5/11.9-DT channel Wave 2/3 from 2026-08-22) | Months 13+ | 1/66 (2%) |
| **Total** | **390** | | | **217/390 (56%)** |

> **Counting basis (re-audited 2026-09-25)**: figures are physical ID-leading table rows within each `## Px` section; delivered = ✅ in the ID or Feature column (inline description ✅ marks don't count). Current: P1 19 / P2 65 / P3 86 / P4 154 / P5 66 = 390 rows; done 19 + 65 + 85 + 47 + 1 = 217. The 2026-09-07 figures (P3 69 / P4 137 / P5 64 = 354, done 175) went stale as later P4 deliveries (evaluation suite 7.2b–7.4a + 7.3a–c, 6.19, 5.9 family, memory 4.x, 1.3.21, 1.3.6f, 5.4a, 5.5d) were never folded in, and that basis is not reproducible by section row grep. Historical notes kept: 2026-08-22 release-scope reclassification moved 48 P3 items into the P4 section; the prior "372 / 168" and P3 "87" figures predate it.

---

## P1: Usable (Months 1-3)

> Users can create Agents, configure models and tools, upload knowledge bases, test via API chat, and deploy on-premises.

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 1.3.1 | ReAct Agent Loop ✅ | Agent Runtime | Standard Think→Act→Observe loop | LangGraph, DeepAgents |
| 1.3.1a | Plan-Execute Task Decomposition ✅ | Agent Runtime | Plan Agent auto-analyzes tasks, selects Skills, decomposes into sub-tasks; supports Guard→Plan→Sub-Agent three-layer architecture | RelayAgent (three-layer Agent), DeepAgents (task tool) |
| 1.3.2 | Tool Calling ✅ | Agent Runtime | Agent calls tools with parallel execution, retry, and timeout support. | Claude Code (40+ tools), OpenClaw (TypeBox) |
| 1.3.3 | Streaming Output ✅ | Agent Runtime | Real-time streaming of Agent responses and tool call progress | LangGraph (7 stream modes) |
| 1.3.4 | Human Intervention ✅ | Agent Runtime | Execution pause, human approval, result correction, resume continue. archive `openspec/changes/archive/2026-08-21-guardrail-upgrade-trio`. | Bisheng (workflow pause), LangGraph (interrupt), dsh approval seam (source-verified), MAF durable approvals, AgentScope PermissionEngine |
| 1.3.5 | Error Recovery ✅ | Agent Runtime | Tool failure retry, model degradation, graceful degradation | OpenClaw (14 failure types) |
| 3.1.1 | Document Parsing ✅ | Knowledge Base/RAG |PDF/Word/PPT/Excel/HTML/Markdown parsing. **Office Open XML supplement (P1 audit, this branch)**: `.| Docling (20+ formats), RAGFlow DeepDoc |
| 3.2.1 | Vector Search ✅ | Knowledge Base/RAG | Embedding + ANN search | LlamaIndex, Qdrant |
| 3.2.6 | Chunking Strategy ✅ | Knowledge Base/RAG |Auto-chunking, character-based, separator-based, semantic chunking.| Coze (4 strategies), LlamaIndex |
| 3.3.1 | Knowledge Base CRUD ✅ | Knowledge Base/RAG | Create, update, delete knowledge bases and documents | Coze, Dify |
| 5.1 | Built-in Tools ✅ | Tools & Plugins | Code execution, Web search, file operations, etc. | DeepAgents (7 file tools), CrewAI (30+ built-in) |
| 5.2 | Custom Tools ✅ | Tools & Plugins | User-defined tools (function/API Schema) | OpenClaw (TypeBox), Claude Code (buildTool) |
| 5.3 | MCP Client ✅ | Tools & Plugins | Connect to external MCP servers, discover and call tools | OpenClaw, OpenCode |
| 5.9 | Skill Loading & Management ✅ | Tools & Plugins | SKILL.md format knowledge/instruction packages, on-demand loading into context; full CRUD API + SKILL. | OpenCode, Claude Code, OpenClaw (ClawHub), RelayAgent, dsh (skill provider registry, source-verified), AgentScope (skill hubs 2.0.6), Hermes Skills Hub (security-scanned) |
| 6.1 | Multi-Model Access ✅ | Model Management | Support 100+ LLM providers | LiteLLM (OpenAI/Anthropic/Google/Baidu/Alibaba/ByteDance…) |
| 6.3 | Model Degradation ✅ | Model Management | Auto-switch to fallback model when primary is unavailable | OpenClaw (14 failure types), HermesAgent (fallback chain) |
| 8.4 | Conversation Logs ✅ | Observability & Operations | Complete conversation history and tool call records | Coze (debug panel), Dify |
| 11.1 | API Interface ✅ | Multi-Channel Access | REST API + WebSocket, OpenAI compatible format | Coze, Dify |
| 13.2 | Self-Hosted Deployment ✅ | Deployment & Operations |Docker Compose / K8s one-click deployment.| Dify, Bisheng |

### P1 Dependency Chain

```
Execution Engine → Model Access → Tool System → Skill Loading → Agent Runtime → Basic RAG → API → Conversation Logs
```

---

## P2: Enterprise-Ready (Months 4-6)

> Visual canvas drag-and-drop workflow builder, multi-Agent collaboration, persistent memory, multi-tenant organization, RBAC, basic evaluation, basic security, basic observability, scheduled tasks, authentication.

### Low-Code Development

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 1.1.1 | Agent Configurator ✅ | Agent Development | Visually configure Agent persona, model, tools, knowledge base, memory | Coze, Dify |
| 1.1.2 | Visual Workflow Canvas ✅ | Agent Development | Drag-and-drop DAG editor with node connections, conditional branching, parallelism. | Coze (DAG), Langflow (React Flow) |
| 1.1.3 | Workflow Node Type Library ✅ | Agent Development | LLM, Code, Condition, Tool, Knowledge Base Retrieval, Variable, Batch Processing nodes. | Coze (8 types), Dify |
| 1.1.4 | Workflow Test Run ✅ | Agent Development | Step debugging, input/output preview, execution logs. | Dify, Coze, LangGraph Studio |
| 1.1.5 | Scenario-based Agent Packaging ✅ | Agent Development | Package configured Agents (persona + tools + knowledge + skills + channels) into reusable scenario solutions | AgentArts (scenario Agent) |

### Multi-Agent Orchestration

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 2.1 | Hierarchical Delegation ✅ | Multi-Agent Orchestration | Parent Agent spawns child Agents, context isolation, result aggregation | DeepAgents (task tool), OpenClaw (subagent) |
| 2.2 | Handoff ✅ | Multi-Agent Orchestration | Agent returns another Agent from tool function, control transfer. | OpenAI Agents SDK (handoff_description), Google ADK (agent.description), AutoGen Swarm, LangGraph (Command(goto=...)) |
| 2.3 | Pipeline ✅ | Multi-Agent Orchestration | Deterministic multi-step process, data flow between stages | CrewAI Sequential, AgentScope Pipeline |
| 2.4 | Broadcast ✅ | Multi-Agent Orchestration | Shared message space visible to all participants | AgentScope MsgHub |
| 2.7a | Collaboration Pattern Selection ✅ | Multi-Agent Orchestration | Select collaboration pattern on canvas: Sequential (linear chain), Parallel (fan-out/merge), Handoff (control transfer), Broadcast (shared context), Negotiation (proposer-responder loop), Debate (alternating arguments). | Coze (Multi-Agent), AgentArts, CrewAI (process types), LangGraph (workflow agents) |
| 2.7b | Agent Communication Configuration ✅ | Multi-Agent Orchestration | Configure inter-agent communication: shared channel selection (readable/writable), message passing protocol, state mapping between agents. | AgentArts (agent config), LangGraph (shared state), AutoGen (message passing) |
| 2.7c | Routing Rule Configuration ✅ | Multi-Agent Orchestration |Configure routing rules for multi-agent workflows: intent-based routing (user intent → agent selection), condition-based routing (data-driven branching), dynamic routing (LLM-driven next-speaker selection).| AgentArts (multi-agent controller), Huawei Pangu (intent routing), AutoGen (SelectorGroupChat) |

### Memory System

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 4.1 | Working Memory (L1) ✅ | Memory System | In-context memory, updated each turn | Letta (MemoryBlock) |
| 4.2 | Session Memory (L2) ✅ | Memory System | Conversation history with auto-compression | Claude Code (5-level compression), dsh (CompactionEngine, source-verified) |
| 4.3 | User Memory (L3) ✅ | Memory System | Cross-session user profile and preferences | Mem0 (extraction + retrieval), HermesAgent (USER.md) |

### Knowledge Base Enhancement

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 3.1.5 | Web Scraping ✅ | Knowledge Base/RAG | Crawl URL content as knowledge source | Crawl4AI |
| 3.2.2 | Keyword Search ✅ | Knowledge Base/RAG | BM25 full-text search | Elasticsearch |
| 3.2.3 | Hybrid Search ✅ | Knowledge Base/RAG | Vector + keyword fusion ranking | RAGFlow, Qdrant (native hybrid) |
| 3.2.7 | Multi-Knowledge Base Association ✅ | Knowledge Base/RAG | One Agent linked to multiple knowledge bases | Coze, Dify |

### Multi-Channel Access

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 11.7 | CLI ✅ | Multi-Channel Access | Command-line interaction | Claude Code, OpenCode |

### Knowledge Base & Agent Enhancement

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 3.2.8 | Knowledge Base Hit Testing ✅ | Knowledge Base/RAG | Pre-deployment retrieval testing, similarity score review, chunking and retrieval quality validation | AgentArts (hit testing) |
| 1.3.7 | Citation Display ✅ | Agent Runtime | Show source citations in responses when using KB or Web search, with traceability | AgentArts (citation display), Coze |
| 1.3.8 | Opening Remarks & Follow-up Suggestions ✅ | Agent Runtime | AI-generated opening remarks and recommended questions, follow-up suggestions after each reply | AgentArts (opening + follow-up), Coze |
| 1.3.9 | Task Queuing ✅ | Agent Runtime |Sequential task processing within a session, new messages auto-queue to avoid concurrency conflicts.| AgentScope (background task offloading), openJiuwen (event-driven multi-agent control) |

### Low-Code Enhancement

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 1.1.8 | Conversational vs Task Workflows ✅ | Agent Development | Two workflow modes: conversational (multi-turn) and task (single execution, API-callable). | AgentArts (dual-mode workflow), Coze (Workflow ↔ Chatflow conversion) |
| 1.1.9 | Workflow Version Management ✅ | Agent Development | Versioned workflow releases, diff comparison, rollback, commit required before publishing | AgentArts (workflow versioning) |
| 1.1.10 | App Import/Export ✅ | Agent Development | Full Agent app import/export for backup, migration, cross-environment replication | AgentArts (app import/export) |
| 6.7 | Model Playground ✅ | Model Management |Built-in model test UI for connectivity and response quality debugging, parameter tuning.| AgentArts (model debugging, A/B 对比调测) |

### Canvas UI Enhancement

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 1.1.14 | Agent Node Config Enhancement ✅ | Agent Development | Enhance Agent node config panel: role description, invocation mode (direct/tool), readable/writable channel selection, model override. | AgentArts (agent config), Coze (bot config) |
| 1.1.15 | Template Customization ✅ | Agent Development | After loading orchestration template, edit Agent roles, add/remove Agent nodes, adjust connections, save as new workflow. | Coze (template edit), CrewAI (crew studio) |
| 1.1.16 | Typed Edge Visualization ✅ | Agent Development | Edge types with visual differentiation: default (solid), handoff (dashed purple), conditional (dotted labeled), fan-out (multi-arrow). | LangGraph (edge types), CrewForm (edge labels) |
| 1.1.17 | Fan-Out/Merge Node Editing ✅ | Agent Development | Make fan-out and merge nodes draggable from palette and configurable (branch targets, merge strategy). | Coze (parallel nodes), Dify (parallel structure) |

### Scheduling & Automation

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 13.9 | Scheduled Tasks ✅ | Deployment & Operations | Cron-triggered Agent/Workflow execution, supports periodic/interval/one-shot, result push to channels | AgentArts (scheduled tasks) |

### Context Engineering

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 4.7 | Context Assembler ✅ | Memory System | Assembles optimized context for LLM invocations from messages, tools, and knowledge | AgentArts (context engineering), dsh (request-time message projections) |
| 4.8 | Evidence Tracker ✅ | Memory System | Captures tool execution results with provenance tracking, importance scoring, and re-reference boosting. | AgentArts (evidence tracking) |
| 4.9 | Task Phase Detection ✅ | Memory System |Detects task phases (EXPLORE/CONVERGE/EXECUTE/VERIFY) for dynamic tool and context filtering.| AgentArts AgentBase (formerly Versatile) (phase detection) |
| 4.10 | Token Budget Governance ✅ | Memory System |Per-session token budget tracking with three-level degradation (DROP/COMPRESS/EMERGENCY) and budget snapshot persistence.| Claude Code (5-level compression) |
| 4.11 | Provider-Shaped Context ✅ | Memory System | Provider-specific context shaping strategies (OpenAI, Anthropic, Default) with automatic model prefix detection. | AgentArts (provider adaptation) |
| 4.12 | Message Prioritization ✅ | Memory System |Prioritizes messages by importance for context window optimization.| openJiuwen (ContextProcessor chain), deer-flow (TokenBudgetMiddleware) |

### Multi-Agent Advanced Coordination

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 2.3a | Agent Message Bus ✅ | Multi-Agent Orchestration | Event-driven pub/sub messaging for multi-agent communication with topic-based routing and broadcast support | AgentScope (MsgHub), AutoGen (GroupChat) |
| 2.3b | P2P Agent Negotiation ✅ | Multi-Agent Orchestration | Peer-to-peer negotiation protocol between agents with multi-round support, timeout handling, and escalation | openJiuwen (agent_evolving) |
| 2.3c | Dynamic Task Allocation ✅ | Multi-Agent Orchestration | LLM-driven task routing to agents with load-aware allocation and capability-based matching | AutoGen (SelectorGroupChat), CrewAI (Hierarchical) |
| 2.3d | Agent-as-Tool Pattern ✅ | Multi-Agent Orchestration | Exposes agents as callable tools for hierarchical delegation, enabling nested agent invocation via EnginePort | Coze (Bot mounts Workflow), watsonx (Agent Node) |

### Validation & Reliability

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 1.3.5a | Output Schema Validation ✅ | Agent Runtime | Validates LLM outputs against expected schemas with auto-repair (trailing commas, missing quotes, JSON extraction from markdown) | AgentArts (output validation) |
| 1.3.5b | Tool Result Validation ✅ | Agent Runtime | Validates tool execution results against JSON Schema with structured error reporting | OpenClaw (TypeBox validation) |
| 1.3.5c | Retry Policy & Circuit Breaker ✅ | Agent Runtime | Configurable retry strategies with exponential backoff, error classification (retryable vs non-retryable), and circuit breaker pattern (CLOSED/OPEN/HALF_OPEN) | OpenClaw (14 failure types) |
| 1.3.5d | LLM Circuit Breaker ✅ | Agent Runtime | Per-prefix circuit breaker for LLM routing — independent breaker per model provider with state transitions | LiteLLM (fallback), HermesAgent (fallback chain) |

### Session Management

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 1.3.9a | Session Locking ✅ | Agent Runtime | Session-level locking for concurrent access control, preventing race conditions in multi-request scenarios | Enterprise standard |

### Prompt Management

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 8.5a | Prompt CRUD & Versioning ✅ | Observability & Operations | Full prompt lifecycle management: create, update, delete, version snapshots, rollback, and label-based deployment (production/staging/development) | LangFuse (Prompt Management) |

### Model Management Enhancement

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 6.2 | Model Routing ✅ | Model Management | Intelligent model selection with 4 strategies (COST/LATENCY/CAPABILITY/BALANCED) and configurable routing rules | LiteLLM (Router) |

### Infrastructure Extensibility

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 13.13 | Multi-Database Support ✅ | Deployment & Operations | Pluggable database backends: PostgreSQL, MySQL, SQLite; SQLAlchemy async dialect abstraction with deploy-time backend selection, `deleted: bool` field replacing PostgreSQL partial indexes | openJiuwen (7 DB backends), Dify (multi-DB) |
| 13.3 | Offline Deployment ✅ | Deployment & Operations | Air-gapped / offline deployment for regulated environments (government, defense, finance). | Palantir Apollo (air-gapped SaaS via cryptographically signed bundles), Bedrock AgentCore (VPC-only MicroVM), openJiuwen (offline installer), 华为 AgentArts (private deployment) |
| 14.2 | Webhook Callbacks ✅ | Deployment & Operations | Outbound webhook callbacks for platform events (agent completed, workflow finished, tool error, threshold alert). | Dify (webhook node), Slack/Stripe webhook patterns (HMAC signatures), openJiuwen (event subscription), AgentArts (消息模板) |
| 3.1.7 | Multi-Vector-DB Support ✅ | Knowledge Base/RAG |Pluggable vector database backends: Qdrant, Milvus, Weaviate, Chroma; unified vector operations interface with per-collection backend selection.| openJiuwen (4 vector DBs), LlamaIndex (vector store abstraction) |
| 5.9a | MCP Server Mode ✅ | Tools & Plugins | Expose Hecate capabilities (Agent execution, Knowledge retrieval, Tool invocation) as MCP tools via MCP server; enables external platforms to consume Hecate as a tool provider | OpenClaw (bidirectional MCP), openJiuwen (fastmcp) |

### Multi-Tenant & Enterprise

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 10.1 | Organization Management ✅ | Multi-Tenant & Enterprise | Multi-org isolation, multiple workspaces under org with owner transfer support | Coze (Spaces), Bisheng |
| 10.2 | RBAC ✅ | Multi-Tenant & Enterprise |Workspace-level role-based access control (admin/editor/viewer) with FastAPI dependency guards.| Bisheng (deep RBAC + user groups) |
| 10.5 | Tenant Isolation ✅ | Multi-Tenant & Enterprise | Data-level tenant isolation — workspace_id FK on 14 unscoped models, vector store payload filtering, Alembic migration with topological backfill, service/API workspace enforcement | Enterprise standard |

### Evaluation Foundation

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 7.1 | RAG Evaluation ✅ | Evaluation & Testing | Faithfulness, answer relevance, context recall | Ragas |
| 7.2 | Agent Evaluation ✅ | Evaluation & Testing | Task completion rate, tool call accuracy, response quality | Bisheng (LLM-as-Judge + human annotation) |

### Security Foundation

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 9.1 | Input Security ✅ | Security & Compliance | Prompt injection detection, PII anonymization, secrets detection via InputSecurityHook (PreLLMHook); per-agent configurable guardrail_config; SANITIZE action for in-flight data transformation | NeMo Guardrails, LLM Guard |
| 9.1a | Output Injection Type Detection ✅ | Security & Compliance | Detect downstream-system injection patterns in LLM output (9. | Bedrock Guardrails Standard tier, DeerFlow SkillScan, OWASP LLM01:2025 Scenario #5 |
| 9.2 | System Prompt Leakage Protection ✅ | Security & Compliance |Detect and block system prompt content exfiltration (OWASP LLM07:2025) via winnowing n-gram fingerprint matching (n=5, window=4, blake2b).| Lakera Red, Promptfoo, Amazon Bedrock Guardrails, OWASP LLM07:2025 |

### Observability Foundation

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 8.1 | Full-Chain Tracing ✅ | Observability & Operations |Trace → Span → Generation hierarchical tracing with OTel context propagation, async SQLAlchemy persistence, and REST API.| LangFuse (Session > Trace > Observation), OpenTelemetry GenAI Semantic Conventions (2025-2026) |
| 8.7 | Audit Logs ✅ (SIEM Pipeline ✅) | Observability & Operations |User operation audit trail, compliance requirements.| Cisco AI Defense (SIEM integration), Enterprise security standard (CEF/LEEF/OCSF) |

### P2 Dependency Chain

```
GraphDSL → Canvas → Workflow Nodes → Agent Configurator → Scenario Packaging → Multi-Agent → Memory
Canvas → Agent Node Config → Template Customization → Typed Edges → Fan-Out/Merge Editing
Canvas → Collaboration Pattern Selection → Agent Communication Config → Routing Rule Config
Authentication → Organization Management → RBAC → Tenant Isolation
Security → Input Security → Guardrails → Output Security
Memory → Context Engineering → Validation → Session Locking
Multi-Agent → Agent Message Bus → Negotiation → Task Allocation
Model Access → Model Routing → Prompt Management
RAG → RAG Evaluation → Agent Evaluation
EventStore → Full-Chain Tracing → Audit Logs
Multi-Database → Multi-Vector-DB → MCP Server Mode
```

---

## P3: Trustworthy (Months 7-14)

> **2026-08-22 release-scope reclassification**: P3 closes at 4 remaining items — 5.4b (MCP Streamable HTTP Server 端), 9.1a (Injection Type Detection), 9.2 (System Prompt Leakage Protection), 6.27 (Browser Automation Tool). 40 zero-code items + 8 shipped-feature enhancements moved to the P4 "Deferred from P3" section; 11.4/11.5/11.9-Discord/Telegram moved to P5 (channel Wave 2/3, trigger-based); 11.8 Intent Recognition & Routing dropped (overlaps chat routing + multi-agent handoff + CONDITION intent routing). Enhancement notes inside ✅ rows below are historical context — the go-forward list lives in the P4 deferred section.

> Full evaluation suite, enhanced security, full observability, **Ops Center**, **Model Hub**, **Enterprise Governance**, multi-channel access, advanced model management, plugin system, **Tool Platform**, advanced RAG, memory enhancement, SSO, canary release, self-evolution, meta-agent operations, data backup, version upgrades, NL2Agent, Trace Annotation, Per-Token-Type Auth, Two-Tier Identity, Human Input/Form Node, Trigger Node, Distributed Session State Store, **Event-Sourced Execution State (1.3.19)**, **Dynamic Orchestration (1.3.18)**, **Run Replay (8.20)**, **Browser Automation (6.27)**. *(re-scope: DSL Conversion + Decision Lineage dropped/deferred; OCR/Table/Layout + Knowledge Graph + Model UI + Canvas Embedding deferred to P5 — see P5 deferred section.)*

### Enterprise

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 10.3 | SSO/LDAP ✅ | Multi-Tenant & Enterprise | Enterprise identity federation via OIDC, SAML, and LDAP. | Bisheng (SSO + LDAP) |
| 10.4 | Quota Management ✅ | Multi-Tenant & Enterprise | Per-tenant API call, storage, compute resource limits | Coze (resource points) |

### Evaluation & Testing

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 7.2a | 40+ Built-in Evaluators ✅ | Evaluation & Testing | Pre-built evaluator library: correctness, hallucination (groundedness validation), toxicity, instruction following, citation relevance, tool selection, format check, programmatic evaluators, tool trajectory scoring, multi-turn task success, multi-turn trajectory quality, safety/h. | AgentArts (40+ evaluators), Google ADK (10 evaluation metrics) |
| 7.6 | Regression Test Set ✅ | Evaluation & Testing | Maintain test datasets, CI/CD integration | Promptfoo (CI/CD integration) |

### Security

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 9.4 | Execution Security ✅ | Security & Compliance |Tool call approval, code sandbox isolation, access control, four-level risk authorization (LOW/MEDIUM/HIGH/CRITICAL, supports once/session/project/global scope). archive `openspec/changes/archive/2026-08-21-guardrail-upgrade-trio`.| OpenClaw (exec approval + sandbox), RelayAgent (four-level risk authorization), AgentScope PermissionEngine (bash static analysis), dsh monotonic guards (source-verified), Claude Code auto mode |
| 9.4a | Granular Operation Approval ✅ | Security & Compliance | Independent security approval config for 40+ operations (bash, write_file, mcp_exec_command, etc. | AgentArts (40+ operation approval) |
| 9.4b | Trusted Workspace ✅ | Security & Compliance | File operations within workspace directory auto-allowed, operations outside require explicit approval | AgentArts (trusted workspace) |
| 9.2 | System Prompt Leakage Protection ✅ | Security & Compliance | Detect and block system-prompt / instruction exfiltration attempts in LLM output (OWASP LLM07) — output-security leg complementing 9. | Lakera Red, Promptfoo, OpenClaw |
| 9.5 | Data Security ✅ | Security & Compliance | PII masking in tool results via ToolResultSecurityHook; configurable storage modes (mask_only/mask_and_encrypt); Fernet encryption for PII mapping; PIIMappingModel ORM + audit; per-agent data_security guardrail config | LLM Guard (PII detection) |

> **Dropped**: 9.2a Content Moderation — model built-in safety layers + OpenAI Moderation API (free, purpose-built) cover most content moderation. 9.8 Full-Chain Network Security — TLS/WAF/API Gateway belong to the infrastructure layer (K8s/Istio/cloud WAF), not platform features. See "Dropped Features" appendix.
| 9.12 | Environment Network Egress Control ✅ | Security & Compliance |Per-environment application-level network egress control for DockerEnvironment.| Claude Code (sandbox.network.allowedDomains/deniedDomains), Codex CLI (destination rules + network_proxy), Dify (Squid proxy + K8s Egress), Google Vertex AI (VPC-SC), Bedrock (VPC-only MicroVM) |
| 9.13 | Sandbox Enforcement Integration ✅ | Security & Compliance |Guarantees that EXECUTE_SANDBOX decisions from ToolAccessPolicy actually execute inside DockerEnvironment/gVisor.| Claude Code (permission+sandbox dual-layer enforcement), Codex CLI (sandbox_mode × approval_policy matrix), Bedrock (Gateway boundary enforcement outside agent code) |
| 9.14 | Structured Security Audit Pipeline ✅ | Security & Compliance |Tool policy decision audit (renamed from SecurityAudit → ToolDecision).| Bedrock (OCSF 99001 audit events with request_id/identity/delegation_chain/per-layer-decisions/latency → CloudWatch), Codex CLI (OpenTelemetry log export), IBM watsonx (Governance Graph), DeerFlow (SandboxAuditMiddleware), AWS Cedar multi-agent (OCSF 99001 per-decision audit) |
| 9.15 | Per-Execution Credential Scoping ✅ | Security & Compliance |Runtime credential isolation: tools receive only their scoped credentials at execution time, not all environment variables.| Codex CLI (two-phase runtime: setup phase has secrets → agent phase secrets removed), HermesClaw/OpenShell (credential stripping from agent + backend credential injection by sandbox), Bedrock (per-tool IAM role + Secrets Manager), Palantir (agent service user + OSDK scope) |

### Observability & Operations

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 8.2 | Real-Time Monitoring ✅ | Observability & Operations | Agent runtime status, error rate, latency | LangFuse |
| 8.3 | Cost Dashboard ✅ | Observability & Operations | Token and cost statistics by user/Agent/session | LiteLLM, LangFuse |
| 8.5 | Prompt Version Management ✅ | Observability & Operations | Prompt versioning, tag-based deployment (production/staging) — subsumed by 8. | LangFuse (Prompt Management) |
| 8.5b | Prompt Analytics & Diff ✅ | Observability & Operations | Version diff/comparison, change summaries (commit messages), per-version performance analytics linked to traces, protected labels (RBAC) | LangSmith (prompt diff), Vellum (release reviews) |
| 8.6 | Alerting ✅ | Observability & Operations | Error rate threshold, cost over budget, latency anomalies. | Enterprise standard |

### Model Management

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 6.4 | Cost Tracking ✅ | Model Management | Token usage and cost statistics. Model Cost Management (G8) complete: per-model and per-workspace cost budgets with z-score anomaly detection, configurable enforcement (alert/block via PreLLMHook), spend forecasting, chargeback reports. | LiteLLM (virtual keys + budget), LangFuse (cost tracking), Portkey (cost tracking) |
| 6.8 | Multi-Auth Support ✅ | Model Management |Model providers support 7 auth methods: Api-key, AK/SK, App-code, custom Header, IAM, HMAC, no-auth; compatible with Huawei Cloud, Alibaba Cloud, Baidu, etc.| AgentArts (7 auth methods), Salesforce Trust Layer (zero retention agreements) |
| 6.11 | Model Classification Management ✅ | Model Management | Model classification by purpose (Chat, Embedding, Completion, Rerank), category filtering, category-level quota control. | AgentArts (model classification), Vertex AI Model Garden |

> **Deferred to P5**: 6.9 Provider Info Enhancement, 6.10 Key Security Enhancement, 6.12 Provider Auth State Management, 6.13 Model Management UI Redesign — UI redesign without users is speculative; defer until 13.1 SaaS Deployment launches and real user feedback exists (Dify spent $30M and years on UI).

### Tools & Plugins

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 5.4b ✅ | MCP Streamable HTTP Transport (2026-07-28 spec) | Tools & Plugins |Server side speaks MCP protocol `2026-07-28` (stateless core, no `initialize` handshake, header-based routing) on a single `/mcp` endpoint.| MCP 2026-07-28 spec (Anthropic, Linux Foundation Agentic AI Foundation), official python-sdk 2.0.0, fastmcp 4 |
| 5.4c ✅ | MCP Server Registry & Connection Management | Tools & Plugins | Unified MCP Server registration, discovery, and connection management. | OpenClaw (MCP discovery + connection pooling), Claude Code (MCP connection management), Amazon Bedrock AgentCore Gateway (session management + auto-reconnect), mcpool (connection pool library) |
| 5.5 ✅ | Plugin System | Tools & Plugins |**Plugin runtime engine**: `plugin.| Dify (plugin daemon + YAML manifest), OpenClaw (in-process + pluginApi compat), AgentArts (UI-driven config), Salesforce (code-first + UI config dual path) |
| 5.5 (TP5) ✅ | Plugin Type Taxonomy + Developer SDK | Tools & Plugins | **8 plugin types** classified by capability: **Tool Plugin** (callable function, new ToolPluginBase), **Extension Plugin** (hook/middleware injection, new ExtensionPluginBase wrapping existing Guardrail Hooks — Google ADK BasePlugin pattern), **Trigger Plugin** (event-driven invo. | Dify (6 plugin types + SDK), Google ADK (BasePlugin callback pattern), OpenClaw (30+ capability types + pluginApi compat), HermesAgent (12+ plugin types), AgentArts (UI-driven plugin creation) |
| 5.5c ✅ | Agent Plugins 1.0 Standard Ingestion | Tools & Plugins |Native ingestion of **Agent Plugins 1.0** packages (open standard by Vercel with OpenAI/Microsoft/Amazon/Cursor/GitHub; Google joined as core maintainer; shipped day-one in Codex/ChatGPT, VS Code, Cursor, Copilot, Kiro) as the **ecosystem-facing third-party plugin format**.| Agent Plugins 1.0 spec (agent-plugins.org), Agent Skills spec (agentskills.io, 25+ frameworks), Codex/ChatGPT/VS Code/Cursor/Copilot/Kiro (day-one support), [OpenClaw](https://github.com/openclaw/openclaw) (4-ecosystem plugin bundles, native `plugin.json` reader), [google/skills](https://github.com/google/skills) "plugins (Skills + MCP servers)" packaging precedent, [Bedraock AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-skills.html) git-as-source blueprint (4 sources: curated/git/S3/path), [IBM watsonx](https://developer.watson-orchestrate.ibm.com/agent_skills/manage_skills) upload-time script deny-list, [AgentScope-Java](https://github.com/agentscope-ai/agentscope-java) four-layer skill composition |
| 5.13a ✅ | Plugin Content Scanning (split from 5.13) | Tools & Plugins | Install-time content scanning for declarative (T4) plugin content — extracted from 5. archive `openspec/changes/archive/2026-08-18-plugin-content-scanning`. | Snyk ToxicSkills (0% FP on top-100 legit bar), Koi Security ClawHavoc + Unit 42, Embrace the Red "Scary Agent Skills" (tag-run >10 codepoints = critical; total >100), CSA Unicode Injection in AI Skills + SKILL.md Agent Context Poisoning, DeerFlow SkillScan, Hermes-agent skills quarantine/lockfile, Dify marketplace-toolkit, Google Gemini Enterprise Governing Agent Skills, FortiCNAPP Skills Scanning (SK-*), IBM watsonx upload-time deny-list, OWASP Agentic Skills Top 10 |
| 5.6 ✅ | Tool Permission Control | Tools & Plugins |Tool-level access control with platform-level `available_when` gating — conditional expressions evaluated at Worker level; LLM cannot see or invoke tools whose conditions are unmet; per-tool approval workflow and scope-based visibility.| Salesforce Agentforce (`available when`), OpenClaw (6-layer filtering pipeline) |
| 5.7 ✅ | Tool Caching | Tools & Plugins | Tool result caching to avoid duplicate calls | CrewAI (custom cache functions) |
| 6.27 ✅ | Browser Automation Tool | Tools & Plugins |Playwright-based browser tool: navigate, click, type, screenshot, extract content, fill forms.| Manus Browser Operator, Claude Code computer use, OpenClaw, Microsoft Playwright MCP |
| 5.14 | Environment Security ✅ | Tools & Plugins | **Umbrella feature for agent execution environment security** *(renamed from 5. | Palantir AIP (Ontology unified security: marking+purpose+role, three permission sources), Bedrock AgentCore (Cedar Policy at Gateway boundary, Lambda MicroVM), Claude Code (permission+sandbox dual-layer, 6 permission modes), Codex CLI (OS-native sandbox, auto_review, two-phase runtime), DeerFlow (GuardrailMiddleware + OAP Passport), Salesforce (Einstein Trust Layer, zero retention), AgentScope (multi-type sandbox, middleware system), 华为 AgentArts (microVM isolation) |

### Advanced Knowledge Base (rescoped)

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|

> **Deferred to P5**: 3.1.2 OCR, 3.1.3 Table Extraction, 3.1.4 Layout Analysis — Docling/Unstructured/RAGFlow/Palantir DocInt have industrialized document parsing; Hecate should integrate, not build. 3.4.2 High-Throughput Retrieval — Qdrant native sharding + replication; a deployment guide covers it.

### Knowledge Graph — deferred to P5

> 3.5.1 Knowledge Graph Construction, 3.5.2 Graph Database Integration, 3.5.3 Community Detection & Summarization — Microsoft GraphRAG + LlamaIndex PropertyGraph are mature open source; KG construction is a specialized domain. Integrate when a KG use case lands. Full rows preserved in the P5 deferred section.

### Multi-Agent Enhancement

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 2.8 | Collaborative Conflict Handling ✅ | Multi-Agent Orchestration | Conflict detection, locking, and resolution when multiple Agents access shared resources. | OpenClaw (session lanes) |
| 2.9 | Unified Skill Registry ✅ | Multi-Agent Orchestration | Unify Tools, Knowledge Bases, Workflows, and sub-Agents as attachable "Skills". | Copilot Studio (Tools + Topics + Knowledge), Agentforce (Actions + subagents), watsonx (Agent + Agentic Workflow), Coze (Bot + Workflow/Chatflow), ADK (LlmAgent + WorkflowAgent) |
| 2.9a | Agent-Workflow Mutual Embedding ✅ | Multi-Agent Orchestration |Agent can invoke Workflow as a Tool (Coze: Bot mounts Workflow as skill).| Coze (Bot + Workflow), watsonx (Agent + Workflow DAG), Agentforce (Subagent + Actions), ADK (LlmAgent as sub_agent of WorkflowAgent), IBM watsonx (nesting depth warning) |
| 2.10 | A2A Protocol ✅ | Multi-Agent Orchestration |Google Agent-to-Agent protocol (Linux Foundation v1.| Google A2A, ADK (A2A client/server), Salesforce (cross-org A2A), IBM (A2A integration) |
| 2.10a | Signed Agent Cards ✅ | Multi-Agent Orchestration |Cryptographic signatures on Agent Cards for identity verification (A2A v1.| OpenAI Agents SDK (handoff_description), Google ADK (agent.description), AutoGen Swarm, LangGraph (Command(goto=...)) |

### Canvas UI Embedding (rescoped)

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|

> **Deferred to P5**: 1.1.18 Agent-Workflow Canvas Embedding, 1.1.19 Unified Skill Selector, 1.1.20 Nested Graph Visualization — Canvas enhancements without user feedback are pure guessing; Dify's collaborative editing (Loro CRDT) is the more advanced direction to aim for when triggered.

### Memory Enhancement

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 4.4 | Knowledge Memory (L4) ✅ | Memory System | Long-term knowledge archive, searchable | Letta (Archival Memory) |
| 4.6 | Memory Isolation ✅ | Memory System | User/Agent/session-level memory isolation under multi-tenant | Mem0 (user_id + agent_id + run_id) |

### Multi-Channel Access & Enhancement

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 11.2 | Web Widget (Simplified) ✅ | Multi-Channel Access |Embedded web chat component — **simplified version (Wave 1, P3)** = 内部 Portal / iframe-embeddable for any Hecate deployment（直接复用现有 `(dashboard)/chat` + 员工 JWT 登录），S。.| Coze, Dify, Intercom (JWT + bundle optimization), Salesforce Enhanced Web Chat (RS256 + identityToken API), Google Dialogflow Messenger (Web Component) |
| 11.3 | Feishu (Lark) ✅ | Multi-Channel Access | Feishu bot SDK integration. Status: 首个 ChannelBase 真实实现——暴露当前 SPI 类型擦除（`raw: object`）等问题并推动 SPI 演进；webhook 签名验证 + tenant_access_token + 互动卡片。. | OpenClaw (Feishu channel), Hermes Agent (multi-platform gateway pattern), AgentScope (channels/feishu) |
| 11.9 | Slack ✅ | Multi-Channel Access | International channel. Status: Slack（M，ChannelBase 第二个实现，验证 SPI 通用性）。. | OpenClaw (20+ channels), Hermes Agent (multi-platform gateway pattern) |

### Deployment & Operations

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 13.4a | Distributed Session State Store (Redis) ✅ (5/5) | Deployment & Operations | Redis-backed hot-path session state cache for multi-replica horizontal scaling. | AgentScope 2.0 (RedisAgentStateStore), AgentArts (fka Versatile) (sandbox snapshot → Redis/object store) |
| 13.5 | Data Backup & Recovery ✅ | Deployment & Operations |Automated data backup and recovery: full/incremental backup scheduling, backup retention policies, point-in-time recovery, cross-region backup replication.| Enterprise standard, K8s Velero |
| 13.6 | Version Upgrade ✅ | Deployment & Operations | Zero-downtime version upgrade management: rolling upgrade with health checks, upgrade preflight validation, rollback capability, database migration automation, feature flag gating. | Enterprise standard, AgentAnywhere Sovereign (7 regions), BLACKBOX (US/EU residency) |

### Failure Analysis & Constraint System

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 7.7a | Failure Classification ✅ | Evaluation & Testing | Classifies failures into 10 types (AgentRx taxonomy): instruction_adherence, information_invention, invalid_invocation, tool_output_misinterpretation, intent_plan_misalignment, underspecified_intent, unsupported_intent, guardrails_triggered, system_failure, inconclusive | AgentRx (failure taxonomy) |
| 7.7b | Constraint Rule Generation ✅ | Evaluation & Testing | Generates constraint rules from failure analysis with priority levels (CRITICAL/HIGH/MEDIUM/LOW) for injection into system prompts | HermesAgent (constraint learning) |
| 7.7c | Constraint Injection ✅ | Evaluation & Testing | Injects generated constraint rules into system prompts to prevent similar failures in future conversations | HermesAgent (constraint enforcement) |

### Meta-Agent Operations

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 13.9a | Meta-Agent Scheduler ✅ | Deployment & Operations | Lightweight async scheduler that invokes meta-agents at configurable intervals without external cron dependencies | openJiuwen (heartbeat), jiuwenswarm (scheduled tasks) |
| 13.9b | Garbage Collector Agent ✅ | Deployment & Operations | Scans expired sessions and orphaned checkpoints; reports resources eligible for cleanup without performing auto-deletion | Enterprise standard |
| 13.9c | Configuration Drift Detection ✅ | Deployment & Operations | Compares actual vs expected configuration dictionaries, categorizes drifts by impact (HIGH/MEDIUM/LOW) and domain (database/LLM/security/performance) | Enterprise standard |
| 13.9d | Compliance Checker Agent ✅ | Deployment & Operations | Runs code style checks via ruff and security configuration audits, producing violation reports with fix suggestions | ruff, pylint |

### Model Management Enhancement

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 6.8a | A/B Testing for Models ✅ | Model Management | Traffic splitting between two models with metrics collection and statistical significance calculation (two-proportion z-test) | LangFuse (Experiments) |
| 6.8b | Gray Release for Models ✅ | Model Management | Gradual model rollout with weighted routing and time-based progressive rollout through configurable stages | AgentArts (canary release) |
| 6.8c | Per-Prefix Circuit Breaker ✅ | Model Management | Independent circuit breaker per model provider prefix (CLOSED/OPEN/HALF_OPEN states) for failure protection | LiteLLM (fallback chain) |
| 6.8d | API Key Encryption ✅ | Model Management | Fernet-based encryption for model provider API keys stored in database | AgentArts (KMS encryption) |
| 6.8e | Model Provider CRUD ✅ | Model Management | Database-backed model provider management with encrypted key storage, provider registry, and connectivity testing | AgentArts (provider management) |

> **Enhancement (O10)**: Model Management Console ✅ — backend APIs for model performance comparison (latency/cost/quality), cost analysis per model, model drift detection via z-score. Frontend monitoring dashboard with Recharts charts deferred to Group 9.
>
> **Enhancement (G4)**: Model Monitoring Dashboard ✅ — per-model latency/cost/error rate trend APIs, model drift detection (z-score on performance metrics), quality regression detection deferred to Sprint 7 (TBD, recorded in roadmap).

### Sandbox Execution Details

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 9.4c | Docker Sandbox Executor ✅ | Security & Compliance | Docker container-based tool execution with CPU (50% default), memory (128MB), and network limits; configurable timeout (30s) and read-only filesystem | E2B, openJiuwen (agent-sandbox) |
| 9.4d | Sandbox Container Pool ✅ | Security & Compliance | Pre-warmed Docker container pool with allocation, recycling, and max-uses retirement policy for efficient sandboxed execution | E2B, openJiuwen (agent-sandbox) |

### Observability Enhancement

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 8.1a | Distributed Tracing ✅ | Observability & Operations | Trace → Span hierarchical tracing with OpenTelemetry-compatible context propagation. | LangFuse (Session > Trace > Observation), Microsoft Agent 365 (auto-OTel), OpenClaw (failover observability) |
| 8.1b | Metrics Collection ✅ | Observability & Operations | Request-level and token-level metrics collection for runtime monitoring | LangFuse, LiteLLM |
| 8.1c | Structured Logging ✅ | Observability & Operations | Structured JSON logging with correlation IDs for log aggregation and analysis | Enterprise standard |
| 8.20 | Execution Replay & Debug Dashboard ✅ | Observability & Operations | Given a session, timeline-replay execution trace-partitioned by `Event. | OMA offline Run Viewer, Conductor web dashboard, Salesforce Session Trace OTel API (session→turns→messages→LLM calls→actions), LangFuse Session/Trace/Observation, dsh `Session.deriveMessages()` JSONL log |

### Authentication System

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 10.6 | Authentication Service ✅ | Multi-Tenant & Enterprise | JWT-based authentication with Argon2 password hashing, token refresh, and API key validation | Enterprise standard |

### Event Sourcing

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 8.8 | Event Store ✅ | Observability & Operations | Append-only event logging with 12 event types (NODE_START, NODE_END, TOOL_CALL, TOOL_RESULT, CHANNEL_WRITE, LLM_REQUEST, LLM_RESPONSE, INTERRUPT, RESUME, ERROR, PII_DETECTED, CUSTOM), version tracking, and replay capability | LangFuse, Event Sourcing pattern |

### Temporal Workflow Support

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 13.10 | Channel Update Conflict Resolution ✅ | Deployment & Operations |Conflict resolution for concurrent channel updates with 4 strategies: LAST_WRITE_WINS, MERGE_LIST, MERGE_MAP, HUMAN_APPROVAL — the ✅ covers the in-process ConflictResolver only; the Temporal durable-execution backend (`runtime/temporal/`) is a placeholder retu.| Temporal, CRDT |

### Resilience & Safety

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 1.3.5f | Platform-Level Tool Gating ✅ | Agent Runtime | Tool definitions support `available_when` conditional expressions evaluated at Worker level — LLM cannot see or invoke tools whose conditions are unmet; hard platform gate, not prompt instruction | Salesforce Agentforce (`available when`), Microsoft Copilot Studio (action gating) |
| 1.3.5g | Unified Exception Hierarchy ✅ | Agent Runtime |`HecateError` base class → `EngineError` / `ChannelError` / `SecurityError` (Hecate-specific errors only); `ErrorCategory` enum for LLM/Tool error classification (replaces full LLMError/ToolError exception tree — 10-platform research shows no platform wraps pr.| Google ADK (`ToolErrorType` enum), LangGraph (graph errors), LangChain (dual-inheritance mapping), OpenAI SDK (status-code hierarchy), Salesforce (failover + circuit breaker) |
| 1.3.5h | Framework-Level Auto-Retry ✅ | Agent Runtime | RetryStrategy ABC + NoRetryStrategy + RetryExecutor (non-streaming + stream-safe retry) + DefaultRetryStrategy (ErrorClassifier + exponential backoff with jitter) + PregelRuntime integration with per-node config override + EventStore observability | Google ADK 2.0 (`RetryConfig`), IBM watsonx (virtual policy retry) |
| 1.3.18 | Dynamic Orchestration ✅ *(see [ADR-032](../design/adr/032-dynamic-orchestration.md))* | Agent Runtime |7th multi-agent pattern: coordinator node transforms goal + agent roster into a runtime task DAG, dispatches workers, synthesizes results.| Magentic-One（arXiv 2411.04468，two-ledger 双循环源协议），OMA v1.14（goal-first 1:1 对标：PlanPatch/consensus/governance/hybrid routing），DeerFlow subagents/AGENTS.md（benefit-based routing + 三轴 cap + additive stop_reason，source-verified），Deep Agents interpreters（六种分发模式 + code-as-plan 动机），AgentScope（team tools + plan tools），dsh（Ralph 循环 / WorkerRun 台账 / SubagentRuntime 能力校验） |
| 1.3.19 | Event-Sourced Execution State / Log-as-Truth ✅ | Agent Runtime | (see [ADR-030](../design/adr/030-event-sourced-execution-state. | dsh session log (source-verified v0.1.0-rc.5), deer-flow DeltaChannel (v2.0.0), MAF Durable extension, OMA checkpoint+resume |

### AIP Capabilities (AgentArts[formerly Versatile]/Palantir-Inspired)

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|

> **Dropped**: 6.17 DSL Conversion Framework — MCP/A2A protocol standardization plus Salesforce open-sourcing Agent Script indicates the industry is converging on standard agent definitions, not DSL compatibility layers. See "Dropped Features" appendix.
>
> **Deferred to P5**: 6.21 Decision Lineage — full decision lineage requires an Ontology foundation (data + function + app version binding per trace, Palantir standard); effort was underestimated in the initial analysis. Full row preserved in the P5 deferred section.

### Platform SPI (Core Infrastructure)

> **Architecture Principle**: Core capabilities (security, multi-tenant, local-deployment, basic eval) are native first-class. Extension capabilities (channels, evaluators, auth providers, notifiers, i18n) are pluggable via SPI (Service Provider Interface). All downstream plugins depend on Plugin SPI Core (5.5a).

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 5.5a | Plugin SPI Core ✅ | Platform SPI | Plugin registration, discovery, lifecycle management, and sandbox isolation ABC. | OpenClaw (100+ extensions), HermesAgent (plugin hooks) |
| 7.2-abc | EvaluatorBase ✅ | Platform SPI | Evaluator uniform interface: `evaluate(input, context) → EvalResult`. | Ragas (evaluator abstraction), Google ADK (evaluation metrics) |
| 11.1-abc | ChannelBase ✅ | Platform SPI | Channel adapter uniform interface: `receive`, `respond`, `stream`. | OpenClaw (channel plugin architecture), Coze (channel SDK) |
| 10.3-abc | AuthProvider ✅ | Platform SPI | Auth provider uniform interface: `authenticate(token, db) → AuthContext None`. | Enterprise standard (SAML/OIDC), Bisheng (SSO + LDAP) |
| 8.6-abc | NotifierABC 🔀 | Platform SPI | Notifier uniform interface merged into ChannelBase (11.1-abc). Mail/Webhook become `NotificationChannelAdapter` built-in implementations; PagerDuty/Slack Bot/DingTalk notifications register as Channel plugins. | PagerDuty API, Enterprise standard |

### Internationalization

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 15.1 | i18n SPI ✅ | Internationalization | Locale passing (Accept-Language header → runtime locale context), message catalog loading mechanism (JSON/YAML), fallback chain (requested → user → workspace → English), parameter interpolation, plugin translation declarations. | Django i18n, FastAPI babel |

### Ops Center & Operations

> Unified administrative control plane consolidating monitoring, evaluation, deployment, cost governance, and compliance into a single operator interface.

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 8.9 ✅ | Unified Ops Center Dashboard | Observability & Operations |Centralized admin console homepage aggregating all operational views: system health summary, active alert count, evaluation pass rates, cost trends, deployment status, recent audit events.| Palantir Control Panel, Salesforce Agentforce Studio, Microsoft Power Platform Admin Center |
| 8.9a ✅ | Agent Health Monitoring Dashboard | Observability & Operations | Per-agent health monitoring with near real-time metrics: uptime, error rate, average latency, escalation rate, user satisfaction score. | Salesforce Agentforce Health Monitoring, Microsoft Agent 365 |
| 8.9b ✅ | Conversation Analytics & Quality Scoring | Observability & Operations | Conversation analytics dashboard: session volume trends, user satisfaction scores, conversation clustering by intent, topic distribution analysis, quality score computation. | Salesforce Agentforce, LangFuse (custom dashboards) |
| 8.9c ✅ | Tool Execution Analytics Dashboard | Observability & Operations | Per-tool execution metrics dashboard: latency percentiles (p50/p95/p99), success/failure rate, error distribution by type, invocation count over time, token cost per tool call. | Salesforce (Session Trace OTel API), Palantir AIP (end-to-end observability) |
| 9.10 | Outbound DLP Engine ✅ | Security & Compliance | Outbound Data Loss Prevention for AI agent workflows — scans data before it leaves to external LLM providers, MCP servers, or third-party APIs. | Control Zero (Gateway DLP), Pipelock (62 patterns), ORION Security (agentic DLP) |

### Model Hub

> Model catalog, lifecycle management, monitoring, deployment, cost governance, fine-tuning pipeline.

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 6.44 | Model Catalog ✅ | Model Management | Browseable/searchable model catalog: capability badges, provider comparison matrix, model discovery and one-click enablement workflow. | Vertex AI Model Garden, Dify model marketplace |
| 6.45 | Model Lifecycle Manager ✅ | Model Management | Versioned model registry with staging channels (dev/staging/prod), promotion workflows with approval gates, deprecation scheduling with automated sunset notifications, rollback support. | IBM watsonx, Vertex AI Model Registry |

### Enterprise Governance

> Budget management, vault integration, and enterprise-grade cost governance.

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 10.7 | Budget Management & Cost Governance ✅ | Multi-Tenant & Enterprise | Platform-wide budget management: per-org, per-workspace, and per-agent spending limits with hard/soft cap enforcement. | Salesforce Digital Wallet, Microsoft Copilot Credits |
| 10.8 | Enterprise Vault Integration ✅ | Multi-Tenant & Enterprise | Integration with enterprise secret management platforms: HashiCorp Vault, AWS Secrets Manager, Azure Key Vault, Google Secret Manager. | HashiCorp Vault, AWS Secrets Manager, Azure Key Vault |

### Tool Platform

> Plugin management, MCP connection, and tool platform infrastructure.

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 5.5b ✅ | Plugin Packaging & Distribution | Tools & Plugins | Plugin packaging format (`.hecate-plugin` bundle), packaging CLI (`python -m hecate. | Claude Code Plugins (directory-based), OpenClaw (ClawHub marketplace + npm-pack), Dify (plugin marketplace + remote debugging) |

### P3 Dependency Chain

> **Note (2026-08-22)**: chains below reference items moved to P4/P5 by the release-scope reclassification — retained as historical execution-order guidance; the P3 close-out set is 5.4b / 9.1a / 9.2 / 6.27 (all four independent, no ordering among them).

```
SSO/Quota → Evaluation (7.2a–7.6b) → Security (9.2a–9.8) → Observability (8.2–8.6)
Resilience → Exception Hierarchy (1.3.5g) → Auto-Retry (1.3.5h) → Tool Gating (1.3.5f)
Plugin SPI Core (5.5a) → EvaluatorBase (7.2-abc) + ChannelBase (11.1-abc) + AuthProvider (10.3-abc) + NotifierABC (8.6-abc)
Plugin SPI Core (5.5a) → i18n SPI (15.1)
Multi-Channel → Web Widget → Feishu/WeCom/DingTalk/WeChat → Intent Recognition (all channels via ChannelBase)
Failure Analysis (7.7a–c) → Meta-Agent Ops (13.9a–d)
Model Management → Cost Tracking → Multi-Auth → Key Security → Model Classification → Model Catalog (6.44) ✅ → Model Lifecycle (6.45) ✅
MCP Gateway → Plugin System (5.5, depends on 5.5a) → Tool Permission (available_when) → Tool Caching
MCP Server → MCP Connection Management (5.4d) → Plugin Packaging (5.5b)
Advanced KB → Incremental Update → Batch Indexing (OCR/Table/Layout deferred P5)
Multi-Agent → Conflict Handling → Skill Registry → Mutual Embedding → A2A Protocol (2.10)
Skill Registry → Canvas Embedding (deferred P5) → Human Input/Form Node (1.1.24) → Trigger Node (1.1.25)
Memory → L4 Knowledge → Memory Isolation → Engine Enhancement
Security → Sandbox Executor → Sandbox Pool
PII Masking (9.5) → Outbound DLP Engine (9.10) → Multi-Point Exfiltration Prevention
Secret Management → Enterprise Vault Integration (10.8) → Dynamic Secrets
Guardrail Hooks (9.1a) → Agent Runtime Protection (9.11) → Stateful Session-Level Monitoring
Security Testing (7.7) → Automated Continuous Red Teaming (7.10) → CI/CD Adversarial Testing
Observability → Tracing (+failoverReason) → Metrics → Structured Logging → NotifierABC (8.6-abc)
P3 Observability (8.0–8.8) → Ops Center Dashboard (8.9) → Agent Health (8.9a) → Conversation Analytics (8.9b) → Tool Execution Analytics (8.9c)
P3 Cost Dashboard (8.3) → Budget Management (10.7)
Authentication → Event Store → Canary Release → Agent Identity → Horizontal Scaling
Environment Management (13.17) → API Management (13.18)
NL2Agent (6.16) → NL2Flow → Workflow Auto-Generation
EventStore → Trace Annotation (6.18) → Evaluation Datasets
EventStore → Decision Lineage (6.21, P5 deferred) → Decision Audit → Compliance
EventStore → Event-Sourced Execution State (1.3.19) → Run Replay (8.20) + Projection Registry (8.21, P4)
Pregel + Collaboration Patterns (2.7a ✅) → Dynamic Orchestration (1.3.18) → runtime task DAG
Skill Loading (5.9 ✅) → Skill Provider Registry (5.9-enh ✅) → Skill Versioning (5.9d ✅) → Skill Dependency Declaration (5.9e ✅) → Skill Auto-Detection (5.9c ✅) → Skill 长尾词法检索 (5.9f) → Composable Skill Packages
Built-in Tools (5.1 ✅) → Browser Automation (6.27) → Computer-use (6.27a, P4)
Auth Service → Per-Token-Type Auth (11.16) → Two-Tier Identity (11.17)
Canvas → Human Input/Form Node (1.1.24) → Trigger Node (1.1.25) → Event-Driven Workflows
CheckpointStore → Distributed Session State Store (13.4a) ✅ (5/5) → Horizontal Scaling (13.4)
```

---

## P4: Intelligent (Months 15-18)

> Self-learning, Hallucination Detection, Agentic RL, Prompt Self-Optimization, Ontology Actions, OAG, Agentic RAG, GraphRAG, Memory Integration, Temporal Memory, Lazy GraphRAG, SDK/CLI development, NL2X, distributed orchestration, intelligent routing, deep research, 5-Level Intent, Object Logs, Simulation, Computer-use (6.27a), DataAgent, VibeCoding, Peer Selection, Agent Teams, Distributed Team Orchestration, **ACP (2.13)**, **Projection Registry (8.21)**, **Atomic File Locks (13.20)**, **Voice Agent Pipeline (11.11)**. *(2026-08-14 re-scope: Browser Automation moved to P3 as 6.27; Knowledge Graph API 3.5.5 + Extended Document Processing 3.1.8 deferred to P5.)* *(2026-08-22 release-scope reclassification: 48 items joined from P3 — see "Deferred from P3" below.)*

### Deferred from P3 (2026-08-22 release-scope reclassification)

> Moved verbatim from P3 to close the release scope. 40 zero-code items + 8 shipped-feature enhancements. P3 retains only 5.4b / 9.1a / 9.2 / 6.27 as close-out work.

#### Shipped-feature enhancements (8) — parent features stay ✅ in P3

| # | Enhancement | Parent feature (P3 ✅) | Description | Effort |
|---|-------------|----------------------|-------------|--------|
| 7.2a-OE8 | Evaluation Metrics Three-Dimension Structuring | 7.2a Evaluators | Organize all evaluators into Effectiveness / Efficiency / Safety dimensions with structured dashboard display | M |
| 7.2a-OE9 | Reasoning Efficiency Evaluator | 7.2a Evaluators | Pregel superstep + tool-call-span reasoning-efficiency metrics; frozen per research — redirect to OTel span attributes consumed by LangSmith/Langfuse | S |
| 8.6-O9 | Incident & Alert Management Console | 8.6 Alerting | Centralized alert management: acknowledgement workflow, history, notification routing, silence rules, escalation policies | M |
| 8.9-O8 | Custom Dashboard Builder | 8.9 Ops Dashboard | Drag-and-drop dashboard editor for personalized operational views | M |
| 8.9b-OE5 | Conversation Topic Clustering + Low-Score Analysis | 8.9b Conversation Analytics | Topic clustering and systematic low-score root-cause analysis | M |
| 13.10-E4 | Saga/Compensation Pattern | 13.10 Channel Update Conflict Resolution | Multi-step workflow rollback when step N fails; compensation actions for steps 1..N-1. Requires a real durable-execution backend (current Temporal integration is placeholder) and explicit idempotency/compensation contracts | M |
| O10+G4-QR | Model Quality Regression Detection | O10+G4 Model Monitoring | Quality-score regression monitoring vs historical baseline (depends on Evaluation Suite items below) | S |
| 6.8-EF5 | Zero Data Retention Policy | 6.8 Multi-Auth | Provider retention-policy declaration + zero-retention routing + data-classification audit trail | M |

#### Zero-code items (40)

| # | Feature | Domain | Description | Effort |
|---|---------|--------|-------------|--------|
| 7.2b | AI-Synthesized Evaluation Dataset ✅ | Evaluation & Testing |LLM auto-synthesizes evaluation datasets from seed data, adversarial + security-compliance samples. **Shipped 2026-09-10**. archive `openspec/changes/archive/2026-09-10-ai-synthesized-evaluation-dataset`.| M |
| 7.2c | Online/Offline Evaluation Tasks ✅ | Evaluation & Testing |Persistent evaluation tasks in two kinds. **Offline task** = `EvaluationTask` definition (dataset × evaluators × `answer_source` manual/pipeline/agent) + async run lifecycle (202 + `pending→running→completed/failed`), optionally invoking the agent under test via `RuntimePort. **Shipped 2026-09-11**. archive `openspec/changes/archive/2026-09-11-online-offline-evaluation-tasks`.| M |
| 7.2d | Trace Backflow Dataset ✅ | Evaluation & Testing | Automated backflow of scored production traces into evaluation datasets — the automated half of backflow (human-verified materialization lives in 7. **Shipped 2026-09-12** archive `openspec/changes/archive/2026-09-12-automated-trace-backflow`. | S |
| 7.2e | Evaluation Report Dashboard ✅ | Evaluation & Testing |Automated evaluation reports over the existing evaluation tables (零 schema 变更，全部按需聚合)。 **Shipped 2026-09-12** archive `openspec/changes/archive/2026-09-12-evaluation-report-dashboard`.| M |
| 7.3 | Workflow Evaluation ✅ | Evaluation & Testing |End-to-end workflow testing + regression testing. **Shipped 2026-09-11**. archive `openspec/changes/archive/2026-09-11-workflow-evaluation`, `openspec/changes/archive/2026-09-13-known-bad-exemption`, `openspec/changes/archive/2026-09-13-eval-publish-gate-versions`.| M |
| 7.4 | Human Annotation ✅ | Evaluation & Testing | Human review queues for production agent outputs: reviewer worklists (`annotation_queues` + `annotation_queue_items`, unique per target, lifecycle pending → claimed → completed \. **Shipped 2026-09-12** archive `openspec/changes/archive/2026-09-12-human-annotation-trace-backflow`. | M |
| 7.4a | Human Score Calibration ✅ | Evaluation & Testing |Machine-vs-human score calibration analytics on the shared `evaluation_task_scores` ledger. **Shipped 2026-09-12**. archive `openspec/changes/archive/2026-09-12-human-annotation-trace-backflow`.| S |
| 9.5a | Sensitive Data Auto-Masking | Security & Compliance | Auto-detect and mask credentials/keys/ID numbers in conversation content and logs | S |
| 9.11 | Agent Runtime Protection | Security & Compliance | Stateful runtime security monitoring: Goal Drift, Tool Chain Escalation, Memory Poisoning, Behavioral Anomaly, Rogue Agent detectors | L |
| 7.10 | Automated Continuous Red Teaming | Evaluation & Testing | CI/CD-integrated adversarial testing, 50+ vulnerability types, multi-turn attack workflows | L |
| 2.10b | Multi-Agent Trust Verification | Multi-Agent Orchestration | Trust scoring, capability attestation, delegation depth limits, revocation, per-step trust gates | M |
| 7.7 | Security Testing | Evaluation & Testing | Prompt injection detection + red team testing foundation | M |
| 13.1 | SaaS Deployment | Deployment & Operations | Managed cloud + VPC deployment, production Helm chart, multi-env values, GitOps | L |
| 13.1a | Canary Release | Deployment & Operations | Agent runtime version management, weighted routing canary | M |
| 13.1b | Agent Identity Service | Deployment & Operations | Agent credential management, inbound/outbound auth, secure inter-service communication | M |
| 13.4 | Horizontal Scaling (K8s harness) | Deployment & Operations | K8s scaling test harness — bundled with 13.1 (decision: engine-layer statelessness done via 13.4a; K8s-side validation ships with 13.1 production Helm chart) | M |
| 13.4b | K8s Scaling Test Enhancements | Deployment & Operations | L2/L3 chainsaw e2e + locust + startup validation + PgBouncer — bundled with 13.1 | M |
| 13.17 | Environment Management & ALM Pipeline | Deployment & Operations | DEV/STAGING/PROD lifecycle, promotion workflows, approval gates | L |
| 13.18 | API Management & Developer Portal | Deployment & Operations | Scoped API keys, usage analytics, developer portal with docs + playground | M |
| 8.10 | CI/CD Evaluation Gating | Observability & Operations | Evaluation regression blocks deployment; Git PR-triggered evaluation | M |
| 8.12 | Agent Catalog Governance & Quality Gateway | Observability & Operations | Pre-publish quality assessment: journey completion, tool accuracy, safety metrics | M |
| 3.3.2 | Incremental Update | Knowledge Base/RAG | New-document incremental indexing without full rebuild | M |
| 3.3.3 | Knowledge Quality Evaluation | Knowledge Base/RAG | Retrieval accuracy, recall, faithfulness evaluation | S |
| 3.4.1 | Batch Document Indexing | Knowledge Base/RAG | Large-scale parallel parsing, chunking, indexing | M |
| 1.1.24 | Human Input / Form Node | Agent Development | Canvas node for structured human-in-the-loop: form designer + approval routing, wraps interrupt() | M |
| 1.1.25 | Trigger Node | Agent Development | Visual entry-point nodes: Webhook / Schedule / Event / MCP triggers | M |
| 4.3a | Memory Engine Enhancement | Memory System | Real-time/async dual-path processing, procedural memory, graph-structured memory, self-evolution | L |
| 4.14 ✅ (2026-09-21) | Memory Importance Scoring | Memory System | importance scoring with anchor tiers (0. **Delivered 2026-09-21**.| real-embedding write path (`embedding_real` flag on `memories` / `knowledge_memories`), consolidation writing-point refresh of `last_confirmed_at`, value-score observability fields, L3/L4 input path real embedding (off when the model is unavailable) | M |
| 4.15 ✅ (2026-09-21) | Multi-Signal Fusion Retrieval | Memory System | relevance-dominated retrieval with bounded multiplicative bias (`MEMORY_FUSION_BIAS_ENABLED`, default off — `final = relevance × decay_mult × importance_mult` clamped to a 0. **Delivered 2026-09-21**.| consolidation value score (confirmation freshness × log-compressed access heat; written as observability by 4.14/4.15, and since 4.25 also the capacity-eviction score — the one sanctioned consumer outside ranking); `memory_access_sessions` distinct-session counter; `last_confirmed_at` exogenous decay anchor; cross-layer normalized merge in `BuiltinMemoryProvider.search_memories` |
| 4.16 ✅ (2026-09-24) | LLM-Managed Memory | Memory System |Umbrella for agent-driven memory management. **Delivered 2026-09-24**.| MemGPT/Letta (function-driven memory), deer-flow/openjiuwen (memory_* tool naming), openjiuwen `MemoryProvider` ABC (contract shape) |
| 4.17 ✅ (2026-09-20) | Memory Pressure Alert | Memory System | `MemoryPressureNudgeProcessor` in the 4. **Delivered 2026-09-20**.| Anthropic context editing (`trigger/keep/exclude` vocabulary), OpenClaw pre-compaction memoryFlush (silent-turn variant deferred to v2), Hermes-agent fail-loud "Consolidate now" directive |
| 4.25 ✅ (2026-09-24) | Layered Memory System | Memory System | memory flush + lifecycle. Flush = `pre_compaction` trigger on the 4.5 trigger bus: best-effort registration at the compaction boundary (`consolidation_flush_windows`, `MEMORY_FLUSH_ENABLED`), at-least… **Delivered 2026-09-24**.| OpenClaw pre-compaction memoryFlush (async-handoff shape), deer-flow memory_flush_hook + three-signal eviction, Codex two-phase consolidation pipeline, Bedrock AgentCore eventExpiryDuration |
| 4.21 ✅ (2026-09-22) | Task Memory | Memory System | `episodes` table + `reflections` list `ReflectionEngine` (extract → score/dedupe → plan/judge → apply) sharing the consolidation engine's `ExtractFn/PlanFn/ScanFn/EmbedFn` seam; `work_context_nodes` + `work_context_edges` materialize approved reflections as a typed graph (5 node types `method / outcome / correction / source / pattern`, 4 edge types `tried_before / led_to / corrected_by / validated_by`) for the Work Context Graph (KM6 / ADR-024 §6); four quality gates — (1) model-isolation: **Delivered 2026-09-22**.| Perplexity Brain "Work Memory Graph" (公开材料中**未披露**,Hecate 自行独立路径选择 ADR-024 §6 schema) |
| 6.16 | NL2Agent / NL2Flow | Agent Development | NL requirement → Agent config / workflow diagram generation | M |
| 6.18 | Trace Annotation | Observability & Operations | Thumbs up/down feedback, multi-dimensional trace labels, one-click to evaluation set | S |
| 5.4a ✅ | MCP Gateway | Tools & Plugins | REST/OpenAPI → MCP tool conversion (import-time projection + spec-driven virtual execution); external MCP server federation (`<target>__<tool>` naming, live-proxied via connection management); unified auth/routing/logging gateway on `/mcp` — caller-scoped `tools/list` (workspace-. archive `openspec/changes/archive/2026-09-08-mcp-gateway`. | AgentArts gateway product card, Bedrock AgentCore Gateway (target model + credential brokering), fastmcp 4 Middleware |
| 5.8 | Enterprise System Integration Framework | Tools & Plugins | ERP/CRM/OA connectors + TP6 per-tool auth scope | M |
| 11.16 | Per-Token-Type Auth Pipeline | Access Channel | Separate auth pipelines per token type (JWT/APIKey/PAT/OAuth), gateway-level routing | M |
| 11.17 | Two-Tier Identity Model | Access Channel | App-level (API Key) vs user-level (JWT) identity separation for granular access + audit | M |
| 5.9-enh ✅ | Skill Provider Registry | Tools & Plugins | Provider registry for skills: `provider` classification (bundled/user/project; `custom` reserved; plugin rows outside rank competition) + deterministic rank shadowing (project > user > bundled) via `resolve_by_precedence` + same-name cross-provider coexistence (dual-index migrati. archive `openspec/changes/archive/2026-09-20-skill-provider-registry`. | deepseek-harness (modelInvocable/userInvocable dual switch + layered rank registry), Claude Code (Enterprise>user>project shadowing, plugin namespace), CubePlex (trust-tier anti-escalation), openclaw/ClawHavoc (scan-gated distribution) |

### Self-Learning & Evolution

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 1.3.5e ◐ (Stage 2 ✅ 2026-09-18) | Hallucination Detection & Mitigation | Agent Runtime |**Stage 1 — citation provenance layer**: tool results entering history are deterministically chunked and prefixed with session-scoped `【N-M】` markers at write time (raw content preserved); a session-level append-only registry resolves markers regardless of pro. **Shipped 2026-09-18** archive `openspec/changes/archive/2026-09-18-grounding-scoring-layer`.| Field research 2026-09 (no surveyed platform ships runtime claim→evidence→action as a primitive; Bedrock Guardrails contextual grounding requires a caller-supplied grounding source — this layer manufactures it), CubePlex CitationMiddleware (in-band provenance), Codex issue #35355 (compaction-induced claim corruption) |
| 1.3.5i ✅ | Deterministic Hooks (Lifecycle Events) | Agent Runtime | Model-independent lifecycle event handlers (PreToolUse, PostToolUse, PreEdit, PostEdit, Stop) configured in settings. archive `openspec/changes/archive/2026-08-21-guardrail-upgrade-trio`. | Claude Code Hooks (Sep 2025, 12 events + tool matchers), Salesforce (before_reasoning/after_reasoning), AgentScope (6 middleware hooks) |
| 1.3.15 ✅ | Agent Environment | Agent Runtime | Unified agent execution environment abstraction. | Bedrock AgentCore (Agent Runtime + Session Storage), AgentScope (Workspace: Local/Docker/E2B), Dify (AgentRuntimeSession + AgentDrive), Claude Code (Working Directory + SessionStore) |
| 1.3.16 ✅ | Agent State Separation | Agent Runtime |Separate volatile AgentState (per-session: conversation buffer, compressed summary, permission context, tool/task sub-contexts) from durable Environment (per-agent: session logs, memory, files).| AgentScope (AgentState vs Workspace separation, AgentStateStore), Bedrock (session storage vs BYO file system), Claude Code (SessionStore adapter: S3/Redis/Postgres) |
| 1.3.15a ✅ | Environment Backend: Docker | Agent Runtime |DockerEnvironment backend implementation for AgentEnvironment — isolates agent file system into Docker containers with persistent volumes.| AgentScope (DockerWorkspace), DeerFlow (AioSandboxProvider), Bedrock AgentCore (microVM per session), Claude Code (container-based sandboxing) |
| 1.3.15b ✅ | Context Offloading | Agent Runtime |Offload oversized conversation context and large tool results to environment persistent storage.| AgentScope (offload_context + offload_tool_result, Offloader protocol), Claude Code (5-layer compaction pipeline, tool result truncation) |
| 1.3.15c ✅ | Sandbox Environment Mount | Agent Runtime |Mount environment into sandbox container — agent's files, tools, and skills are directly accessible inside the sandbox at `/mnt/env`.| Bedrock AgentCore (managed session storage at `/mnt/workspace`, 14-day TTL, stop/resume), AgentScope (DockerWorkspace bind-mount), Claude Code (self-hosted sandbox per session) |
| 1.3.17 ✅ | Agent Invocation Mode | Agent Runtime |Upgrades `AgentExecutionPort.agent_execute()` to full LLM pipeline parity with LLMWorker: tool loading from AgentModel, knowledge base retrieval, PreLLMHook/PostLLMHook guardrails, context assembly, token budget management.| OpenAI Agents SDK (invocation patterns), LangGraph (subgraph invocation), Coze (Bot mounts Workflow), watsonx (Agent Node) |
| 1.3.6 | Self-Learning Agent Runtime ⊕ superseded by 1.3.6f | Agent Runtime | Automated end-to-end evolution cycle. **⊕ Superseded**: the 1.3.6a–e skeleton (rule-based heuristics, in-memory, zero callers) was retired and rebuilt as the skill-package closed loop 1.3.6f after the 2026-09 industry survey converged on SKILL.md artifacts + eval gates + human review (AgentCore Optimization, Salesforce self-improving agents, Manus, Warp×Claude) | HermesAgent (closed learning loop), openJiuwen (agent_evolving) |
| 1.3.6a | Trajectory Analysis ⊕ superseded by 1.3.6f | Agent Runtime | Rule-based skeleton (hand-built TrajectoryPoint lists, md5 ids) retired 2026-09-15; rule *pre-filtering* + LLM attribution now live in `studio/self_evolution/` (skill-evolution-pipeline / skill-attribution specs) | HermesAgent, openJiuwen |
| 1.3.6b | Policy Evolution ⊕ superseded by 1.3.6f | Agent Runtime | In-memory ±0.1 priority dict skeleton retired 2026-09-15; policy evolution now manifests as published learned skills with structured deltas | openJiuwen (textual gradient optimization) |
| 1.3.6c | Evolution Integration ⊕ superseded by 1.3.6f | Agent Runtime | Bridge-with-no-callers retired 2026-09-15; the real integration is `EvolutionPipeline` on MetaAgentScheduler | HermesAgent (Curator) |
| 1.3.6d | Synthetic Environment Generation ⊕ deferred | Agent Runtime | Difficulty-banded skeleton retired 2026-09-15 without replacement; synthetic training environments remain an open P4 item to revisit after the skill loop gets production feedback | openJiuwen (auto_harness) |
| 1.3.6e | Self-Evolution Closed Loop ⊕ superseded by 1.3.6f | Agent Runtime | The six-stage loop existed only as catalog prose plus orphan code; delivered for real as 1.3.6f (2026-09-15) | HermesAgent (closed learning loop), openJiuwen (agent_evolving) |
| 1.3.6f ✅ (2026-09-15) | Self-Evolution Skill Loop | Agent Runtime | Closed loop over knowledge-only learned skills: quality signals (score threshold / user correction) harvest completed conversations → rule pre-filter + LLM attribution (AgentRx taxonomy) → candidate skills (procedure + guardrails partitions, structured deltas, theme merge, conten. **Shipped 2026-09-15**. | AgentCore Optimization (recommendation→A/B→promote), Salesforce (frozen-weight loop, "no persistence without verification"), ACE (structured deltas), GEPA (reflective attribution), Manus (approval-gated project learning), Warp×Claude (scheduler-driven improver) |
| 1.3.10 | Multi-Level Intent Recognition ⊕ superseded by 6.23 | Agent Runtime | Hierarchical intent recognition: atomic intent (single user query) → workflow intent (multi-turn complex task) → session intent (overall dialogue goal). **⊕ Duplicate with 6.23 (5-Level Intent Recognition); 6.23 is the superset (adds caching + controller self-evolution). Merged into 6.23 — delivered in `intent-recognition` (2026-09-13 archive).** | AgentArts (multi-agent controller), intent recognition |
| 1.3.20 ✅ (2026-09-14) | Agent Versioning & Channel Publishing | Agent Runtime | Agent-level versioning + channel binding, analogous to workflow versioning (1. **Shipped 2026-09-14**. | AgentArts (提交版本并分享 lifecycle), Salesforce (Versioned Apex, version-bound deployment), LangGraph (thread versioning per deployment), OMA v1.14 (artifact versioning) |

### Agentic AI (Moved from P3)

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 6.15 | Agentic RL Framework | Agent Intelligence | Data flywheel: trace collection → labeling → RL training → model update. Async RL framework, reward mechanisms (rule-based, generative, credit assignment), interaction environments, optimization algorithms. | AgentArts Agent Self-Optimization (formerly Versatile) |
| 6.19 ✅ (2026-09-15) | Prompt Self-Optimization | Agent Intelligence | 评估数据集驱动的多轮 prompt 自优化闭环（编排层变更，底座全复用）：run 钉定 prompt 版本 + 被测 agent + 命名数据集版本（7. **Shipped 2026-09-15**. | AgentArts AgentStudio (formerly Versatile), Amazon Bedrock AdvPO, Vertex AI Prompt Optimizer, GEPA (Agrawal et al. 2025), ACE (Stanford/SambaNova 2025), Braintrust Loop, Hermes-agent |
| 6.20 | Ontology Action System | Knowledge Base/RAG | Define Actions (operations that modify objects/write back to systems), Agent executes via Action Tool. Supports manual/auto execution modes with pre-execution approval. | Palantir AIP Actions, AgentArts (fka Versatile) Ontology Orchestration |
| 6.22 | OAG (Ontology-Augmented Generation) | Knowledge Base/RAG | RAG + Logic + Actions complete closed loop. LLM not only retrieves knowledge but also reasons and executes actions, writing back to source systems. | Palantir OAG |

### Distributed Orchestration

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 2.5 | Peer Selection (Selector) | Multi-Agent Orchestration | LLM selects the next speaker in multi-agent deliberation | AutoGen SelectorGroupChat |
| 2.11 | Agent Team Templates | Multi-Agent Orchestration | Pre-built multi-agent team patterns (Debate, Research, Code Review, Brainstorm, Hierarchical Task); reusable team configurations with role definitions, interaction protocols, and termination conditions | openJiuwen (agent_teams), AutoGen (Team patterns) |
| 2.13 | ACP (Agent Client Protocol) Support (NEW) | Multi-Agent Orchestration |External coding agents (Claude Code, Codex, Gemini CLI) as worker nodes in Hecate orchestration.| OMA (ACP support), Trinity (multi-runtime: Claude Code/Codex/Gemini CLI per agent), deer-flow (Claude Code ACP adapter), dsh (subagent seam, source-verified) |
| 13.15 | Distributed Team Orchestration | Multi-Agent Orchestration | Cross-process agent team formation: agent discovery, registration (capability advertisement), Redis-backed cross-session EventBus, remote task allocation; builds on P2 EventBus/TaskAllocator ABCs and P3 A2A Protocol (2.10) with ZMQ/gRPC transport | openJiuwen (spawn_member + remote bootstrap), Temporal, Google A2A |
| 1.3.11 | Asynchronous Execution API Mode | Agent Runtime | Third execution mode for long-running workflows (minutes to days): submit workflow → receive task_id immediately → poll status endpoint or subscribe to webhook for completion. Complements existing sync (blocking) and streaming (SSE) modes. Eliminates client-side timeout risk for complex multi-step workflows (batch processing, report generation, multi-round research). Task lifecycle: submitted → running → completed/failed/cancelled. Supports cancellation via DELETE on task_id. | Coze (3 API modes: sync 5min/streaming 15min/async 24h), Dify (Celery worker-based async execution + task ID polling) |

### Context Engineering Enhancement

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 4.13 | Context Engine Processor Chain ✅ | Memory System | Evolve ContextEngine from fixed 3-method ABC (select/compress/estimate) to pluggable processor pipeline. | openJiuwen (ContextProcessor chain), AgentScope (ContextConfig + Offloader), Claude Code (5-level compression cascade), dsh CompactionEngine + surface replacement (source-verified), deer-flow TokenBudgetMiddleware |

### High-Code Development

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 1.2.1 | Python SDK | Agent Development | Code-level definition of Agents, tools, workflows, memory | CrewAI SDK, AutoGen AgentChat |
| 1.2.2 | TypeScript SDK | Agent Development | Same as above, TypeScript ecosystem | LangChain.js |
| 1.2.3 | CLI Development Tools | Agent Development | Command-line create, test, deploy Agents | Claude Code, OpenCode |
| 1.2.4 | Code Sandbox | Agent Development | Secure execution of user code (Code tool in LLM nodes) | E2B, Docker |
| 1.2.5 | Local Dev Environment | Agent Development | Run Agents locally, hot reload, breakpoint debugging | DeepAgents CLI |
| 1.2.6 | Managed Runtime | Agent Development | Platform-hosted high-code Agent runtime, container isolation, auto-scaling, health checks | AgentArts (managed runtime) |

### Low-Code & Intelligent Generation

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 1.1.7 | NL2Agent | Agent Development | Natural language requirement description, auto-generate Agent config or workflow | AgentArts NL2Agent |
| 1.1.11 | NL2Workflow | Agent Development | Natural language directly generates complete workflow | AgentArts (NL2Workflow) |
| 1.1.13 | Workflow Self-Optimization | Agent Development | Text gradient optimization based on hierarchical feedback and local contribution, auto-adjusts prompt effects across workflow nodes, end-to-end pipeline quality improvement | AgentArts AgentStudio (formerly Versatile) (workflow self-optimization) |

### Multi-Agent Communication

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 2.5a | Expert Panel Deliberation | Multi-Agent Orchestration | Multi-agent debate/deliberation: structured discussion protocol, consensus-building, voting mechanisms, with configurable panel composition and moderator roles | AutoGen (debate), AgentArts (expert panel) |
| 2.6 | Inter-Agent Communication | Multi-Agent Orchestration | State mapping, shared memory blocks, message passing | Letta (shared MemoryBlock), A2A protocol |
| 2.6a | Multi-Agent Central Controller ✅ | Multi-Agent Orchestration | Shipped in `intent-recognition`: CONTROLLER node (per-turn recognition via the 6. | AgentArts (multi-agent controller) |

### Canvas UI Orchestration

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 1.1.21 | Multi-Agent Controller Canvas ✅ | Agent Development | Shipped in `intent-recognition`: CONTROLLER node with intent-package picker (published versions + pin, defaults to latest published), mapping editor rendered from the selected version's categories (removed categories flagged for reassignment), start/default/end selectors, global-. | AgentArts (multi-agent controller), Huawei Pangu (controller) |
| 1.1.22 | Orchestration Mode Switching | Agent Development | Canvas mode toggle: Sequential (linear chain), Parallel (fan-out/merge), Conditional (branch/merge), Intent Routing (controller pattern). Mode-specific node palette and connection rules. | CrewAI (process types), LangGraph (workflow agents) |
| 1.1.23 | Execution State Visualization | Agent Development | Real-time agent status on canvas during execution: which Agent is running (animated border), which edges are active (highlighted), error states (red), completion (green). Step-through debugging. **Planned enhancement (G5)**: Workflow Analytics Dashboard — per-workflow execution metrics (success rate, average duration, bottleneck nodes, frequent failure points) displayed inline in Studio. **Planned enhancement (G7)**: Agent Debug Inspector — superstep-level state inspector showing Channel values, node inputs/outputs, and execution timeline at each BSP barrier. | CrewForm (live execution), LangGraph (streaming visualization), LangGraph Studio (debug inspector) |
| 1.1.26 | Object CRUD Node | Agent Development | Ontology-aware canvas node types for Knowledge Graph operations: Create Entity, Update Entity, Delete Entity, Query Entity (Cypher/template), Create Relation, Traverse Subgraph. Each node maps to GraphStore ABC operations via EnginePort.knowledge_query. Type-safe parameter binding from ontology schema definitions — properties autocomplete from entity type metadata. Replaces generic Tool node wrapping with dedicated ontology development experience. **AgentArts 形态注记 (2026-09-06)**: AgentArts 对象模板 is a lightweight schema asset (named object + variables + identity/attributes, no KG dependency) referenced by workflow memory-variables/start-node/object-extraction nodes for structured data extraction. When implementing 1.1.26, consider Phase 1 = lightweight schema assets + extraction-node binding (no KG), Phase 2 = full KG-backed CRUD — avoids gating simple extraction use-cases on the P5 KG dependency. | AgentArts (formerly Versatile, Object management/extraction nodes), Palantir AIP (OSDK typed CRUD) |
| 1.1.27 | Side-by-side Chat + Canvas | Agent Development | Integrated development view: workflow canvas on the left, real-time chat preview on the right. Developers edit the graph while simultaneously testing the agent — no page switching. Chat messages stream live alongside the canvas, with active node highlighting showing which graph node generated each response segment. Test data, session state, and tool call results visible inline. | Coze (canvas + chat), Dify (test panel), AgentArts (online debugging) |

### Advanced RAG

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 3.1.6 | Multi-Modal Documents | Knowledge Base/RAG | Image, audio, video content processing | Docling (audio pipeline) |
| 3.2.4 | Reranking | Knowledge Base/RAG | Search result re-ranking for quality improvement | LlamaIndex (Reranker) |
| 3.2.5 | GraphRAG | Knowledge Base/RAG | Knowledge graph + community reports, cross-entity reasoning | Microsoft GraphRAG |
| 3.2.9 | FAQ Search Mode | Knowledge Base/RAG | Dedicated FAQ Q&A pair matching, returns answer directly if threshold exceeded | AgentArts (FAQ search) |
| 3.2.10 | Agentic RAG | Knowledge Base/RAG | Iterative retrieval with agent-driven query reformulation and multi-step reasoning. Agent decides whether to retrieve more, refines queries based on initial results, evaluates retrieval quality (self-reflection), and chains multiple retrieval steps. Graph node support for RAG loops (retrieve → evaluate → reformulate → re-retrieve). | LangGraph (agentic RAG patterns), LlamaIndex (query engines), Google ADK (retrieval agents) |
| 3.3.5 | Chunk Editing | Knowledge Base/RAG | Manual editing/adding chunks after auto-chunking for fine-tuning retrieval quality | AgentArts (chunk editing) |
| 3.4.3 | Distributed Storage | Knowledge Base/RAG | Knowledge base data sharding, cross-node storage | Milvus (distributed mode) |
| 3.5.4 | GraphRAG Query Engine | Knowledge Base/RAG | Knowledge graph-based retrieval engine: Global Search (community summary map-reduce), Local Search (entity neighborhood traversal), Hybrid Search (vector + graph traversal fusion). Multi-granularity retrieval combining structural and semantic signals. **Planned enhancement (KM4)**: DRIFT Search mode — entity fanout combined with community context, bridging Local and Global search. Provides focused multi-hop reasoning with community-aware pruning, avoiding irrelevant subgraph expansion. **Planned enhancement (KM5)**: Schema-Aware Traversal — integrate SHACL/Ontology schema constraints into graph traversal. Structure-first retrieval where schema constraints prune the search space before semantic scoring, preventing semantic supernodes from causing uncontrolled search expansion in dense enterprise KGs. **Blocked (2026-08-14)**: depends on 3.5.1-3.5.3 (P5 deferred) — rebase on GraphRAG/LlamaIndex integration when triggered. | Microsoft GraphRAG (DRIFT Search), LightRAG (dual-level retrieval), SCAIR (schema-conditioned traversal, ACL 2026) |
| 3.5.6 | Agent-Native Graph Memory | Memory System | Integrate knowledge graph into Agent memory system: auto-extract entities/relationships from conversations, persist to graph database, support graph-aware context assembly. **Blocked (2026-08-14)**: depends on 3.5.2 (P5 deferred). | Google ADK neo4j-agent-memory |
| 3.5.13 | Temporal Memory & Reasoning | Memory System | Time-aware memory with temporal query support. Memories carry temporal metadata (valid_from, valid_to, superseded_by) enabling queries like "Where did the user live before SF?", "What was the project status last month?". Retrieval ranks by temporal relevance — current facts outrank historical ones for present-tense queries, historical facts surface for past-tense queries. Handles fact supersession: when a fact changes (e.g., user changes job), old fact is preserved with valid_to timestamp, new fact created with valid_from. Temporal inference resolves implicit ordering ("before X", "after Y", "when Z was true"). Benchmark: Mem0 reports +29.6 points on temporal queries with this approach. | Mem0 v2.0 (temporal reasoning, +29.6 points), Perplexity Brain (session timeline) |
| 3.5.14 | Lazy GraphRAG | Knowledge Base/RAG | Cost-optimized graph indexing variant for large-scale enterprise corpora. Defers full entity extraction and community detection to query time, using lightweight NER + concept hashing for initial index. Full GraphRAG community summaries generated on-demand for queried subgraphs only. Index cost ~0.1% of full GraphRAG, query cost ~4% of Global Search while matching quality. Progressive enrichment: frequently-queried subgraphs accumulate full community summaries over time, converging toward full GraphRAG quality. Ideal for large document sets (>100K pages) where full indexing is cost-prohibitive. | Microsoft LazyGraphRAG (0.1% indexing cost), LightRAG (lightweight dual-level retrieval) |

### Memory Integration

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 4.5 ✅ (2026-09-20) | Sleep-time Memory Consolidation | Memory System | plan-then-apply engine — per-unit bounded LLM (extract candidates → typed operations ADD/UPDATE/SUPERSEDE/NOOP/UPDATE_BLOCK) applied by deterministic code through the shared service-layer write path (revision guards + `memory_edit_log` with `tool_name=consolidation`); SUPERSEDE lineage (`superseded_by` pointer + soft delete, never physical delete); `consolidation_runs` run-level audit doubling as per-unit watermarks (latest SUCCESS `window_end`); trigger bus = cron (default 02: **Delivered 2026-09-20**. null); per-run budgets (LLM calls / mutations); all behind `CONSOLIDATION_ENABLED` (default off). **Shipped 2026-09-20**.| ADR-024 KM3 (design), Letta sleep-time compute, Perplexity Brain overnight synthesis, OpenJiuwen Dreaming (checkpointed sweep + shared write path) |
| 4.18 ✅ (2026-09-19) | Conversation Recall Storage | Memory System | transcript-level recall layer — `recall_messages` metadata table + Qdrant `hecate_recall` collection, background indexer projecting `CHANNEL_WRITE` (messages-channel) events with per-session version watermarks (idempotent, self-healing), workspace/agent-scoped with Qdrant payload filtering; `conversation_search` tool (query/time-window/roles/cursor/exclude_session_ids) behind `RECALL_INDEXING_ENABLED`; outlives event retention by design (retention never touches recall rows; conversation deletion cascades). **Delivered 2026-09-19**.| Letta (`conversation_search` hybrid signature), OpenClaw (write-time transcript FTS + dual-lane), cubeplex (hybrid RRF + background indexer), AWS namespace scoping |
| 4.19 ✅ (2026-09-19) | Self-Editing Memory | Memory System | L1 block editors with layered semantics — `memory_replace` (exact match once; ambiguity → structured error; empty new_string deletes), `memory_insert` (line insertion), `memory_rethink` (whole-block rewrite, no auto-create); L3/L4 corrections via `memory_update`/`memory_forget` with `revision` optimistic concurrency (`expected_revision` guard, conflict errors carry current revision) and `memory_edit_log` audit trail (before/after summaries, agent/session attribution; not writable by any tool). **Delivered 2026-09-19**.| Letta (replace/insert/rethink semantics), Hermes-agent (old_text ambiguity refusal), deer-flow (fact-level revision optimistic concurrency), OpenClaw (audit trail + immutable provenance), Codex (git-based memory audit) |
| 4.20 ✅ (2026-09-19, rescoped) | Retrieval Escalation (was Multi-Step Memory Retrieval) | Memory System |**Rescoped during 2026-09 field research**: the MemGPT heartbeat reference is retired (removed in Letta's own successor; no 2026 platform ships an in-turn forced retrieval loop). **Delivered 2026-09-19**.| OpenClaw Active Memory (escalate gating — replaces heartbeat), Hermes-agent (`exclude_session_ids` iteration), deer-flow (token-budget/anti-thrash middleware shape) |
| 4.22 ✅ (2026-09-22) | Tool Memory | Memory System | same store as 4.21 — tool calls land as `TOOL` events in the parent episode's `actions`; `ReflectionEngine` analyzes `actions` + `outcomes` together when extracting typed reflections, so the tool expe… **Delivered 2026-09-22**.| AWS Bedrock AgentCore EpisodicMemoryStrategy (tool event as TOOL event) |
| 4.23 ✅ (2026-09-22) | Cross-Thread Memory Store | Memory System | extends existing two-layer namespace (`workspace_id + user_id`) on `memories` + `knowledge_memories` to four layers `workspace_id + team_id + actor_id + session_id`. **Delivered 2026-09-22**.| AWS Bedrock AgentCore namespace (`actorId` + `sessionId` + IAM-style namespace keys) |

### Tool Enhancement

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 5.4 | MCP Server | Tools & Plugins | Expose Hecate capabilities as MCP tools | OpenClaw (bidirectional MCP) |
| 5.9c ✅ (2026-09-23) | Skill Auto-Detection | Tools & Plugins |Skills become discoverable without manual agent-skill association: an L1-catalog discovery pool of eligible workspace + bundled skills (`model_invocable`, non-`user` provider, plugin-enabled, precedence-collapsed, workspace trust-floor filtered) is injected al. **Shipped 2026-09-23** archive `openspec/changes/archive/2026-09-24-skill-auto-detection`.| Claude Code Skills 2.0 (Jan 2026) |
| 5.9f | Skill 长尾词法检索 | Tools & Plugins | Model-invoked `search_skills(query)` builtin — deterministic lexical retrieval (term-coverage ranking, bounded results) for workspaces whose skill pool exceeds the L1 catalog budget; no embedding dependency | 5.9c |
| 5.9d ✅ (2026-09-21) | Skill Versioning | Tools & Plugins |Immutable skill snapshots with full lifecycle: commit / list / get / diff / rollback / delete, plus drift detection. **Delivered 2026-09-21**.| Claude Code Skills (version tracking), OpenClaw (ClawHub versioning) |
| 5.9e ✅ (2026-09-22) | Skill Dependency Declaration | Tools & Plugins | SKILL.md frontmatter `requires` (list of `{name, provider?}`) declares inter-skill dependencies; `plugin. **Shipped 2026-09-22** archive `openspec/changes/archive/2026-09-22-skill-dependency-declaration`. | npm (dependency resolution), Helm (Chart.lock: declare loose, lock at build), Claude Code plugin dependencies (install-time resolve, load-time check) |
| 5.12 | MCP Sandbox Security | Tools & Plugins | Sandboxed MCP tool execution with resource limits (CPU, memory, network), tool-level permission policies, and audit logging for external MCP server calls | openJiuwen (MCP sandbox), E2B |

### Model Enhancement

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 6.5 | Self-Hosted Inference ✅ | Model Management | Locally deployed models (vLLM/Ollama). Managed Model Deployment (G5) complete: InferenceBackendABC, OpenAICompatibleBackend, endpoint registration, periodic /health polling, Prometheus metrics collection, health-based routing. | vLLM (PagedAttention), Ollama, Salesforce BYOM |
| 6.6 | Model Fine-Tuning ✅ | Model Management | Fine-tune models on business data. | Baidu Qianfan (8+ fine-tuning methods), Bailian, Vertex AI Model Tuning |
| 6.14 | Intelligent Router with Caching | Model Management | Rule-based + LLM semantic routing with response caching (semantic similarity hit detection), automatic fallback chains, cost-aware routing optimization, and cache invalidation strategies. **Enhancement (AgentArts 形态对齐, 2026-09-06)**: policy as a nameable, bindable entity — named routing policies (model group + total timeout ms + per-model retry count) selectable in an agent's model config, so an agent binds a policy instead of a single model and requests auto-select the optimal model. Ships with 6.14. | openJiuwen (IntelliRouter), LiteLLM (Router + caching), AgentArts (路由策略) |
| 6.47 ✅ (2026-09-14) | Model Service Publishing | Model Management |Wire the published/unpublished lifecycle (already engineered as 6. **Delivered 2026-09-14**.| AgentArts (model service publish lifecycle), 6.45 Model Lifecycle Manager ✅ |
| 6.48 ✅ (2026-09-14) | Model Management Quick Wins | Model Management |Two low-cost items pulled forward from the AgentArts comparison (pure frontend + one aggregation query, no schema change): (1) list-level search/filter for providers and models on settings/models (AgentArts has keyword search on both levels; Hecate has none); . **Shipped 2026-09-14**.| AgentArts (provider card stats, list search) |

### Evaluation Enhancement

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 7.5 | A/B Testing (rescoped to Agent-Level, 2026-08-14) | Evaluation & Testing |Agent/version-level controlled experiments: split traffic across two agent versions (or two agent configurations), collect quality metrics, z-test statistical significance.| Salesforce A/B Testing API (pilot, agent versions), 6.8a ✅ (z-test machinery) |
| 7.8 | Agentic RL Optimization & Data Flywheel | Evaluation & Testing | Trace data backflow → auto-labeling → RL optimization → model/Prompt update, forming Agent self-evolving data flywheel | AgentArts AgentOps (formerly Versatile) (data flywheel + AgenticRL) |
| 7.9 | Testing Center / Sandbox | Evaluation & Testing | Dedicated testing UI for ad-hoc and batch agent testing: create test suites from production traces or synthetic data, run parallel evaluations across multiple agent configurations, view side-by-side result comparisons, regression detection. Supports sandboxed test execution isolated from production data. **Planned enhancement (OE1)**: CI/CD Evaluation Gating — evaluation results integrated with deployment pipeline; evaluation score regression automatically blocks deployment; supports Git PR-triggered evaluation. | Salesforce Testing Center, Dify sandbox mode, Palantir AIP Evals, Braintrust (CI/CD eval gating), LangSmith (deployment evaluation) |

### Security & Integration

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 9.3 | Retrieval Security | Security & Compliance | Retrieval result injection detection, knowledge base access control | NeMo Guardrails (retrieval rails) |
| 9.7 | Network Isolation | Security & Compliance | Agent execution network sandbox, outbound/inbound traffic control, domain whitelist/blacklist | openJiuwen (network isolation), Docker network |
| 9.16 | External Policy Engine Interface | Security & Compliance |Pluggable external policy engine interface (PolicyEngineABC).| Bedrock AgentCore (Cedar: forbid-wins, default-deny, SMT formal verification, NL→Cedar generation) + **Dogwood temporal policies (2026-08-06, Apache 2.0)**, DeerFlow (GuardrailProvider protocol + OAP Passport), HermesClaw (OPA + Rego), Zylos Research (hybrid: Cedar for structural auth + Casbin for hard limits + custom scoring for anomaly tracking) |
| 9.16a | Chaos Engineering for Multi-Replica | Security & Compliance |L3 chaos engineering for 13.4/13.4a multi-replica state store.| Netflix Chaos Monkey (chaos engineering origin), chaos-mesh (CNCF sandbox, network/pod/io failure injection), toxiproxy (Shopify, deterministic latency/timeout simulation), Litmus Chaos (CNCF, K8s-native chaos), cilium nightly scale-test (continue-on-error "failures reviewed, not gating") |
| 9.17 | AI Auto-Approval | Security & Compliance |AI-driven automatic approval for low-risk tool calls.| Codex CLI (auto_review: AI sub-agent reviews eligible approval requests), Claude Code (auto mode: classifier evaluates action safety with hard_deny/soft_deny/allow tiers) |
| 3.3.4 | Third-Party KB Integration | Knowledge Base/RAG | Import external knowledge bases (Confluence, SharePoint, Notion, Websites) | Unstructured, LlamaIndex connectors |

### Deep Research

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 5.11 | Deep Research | Tools & Plugins | Multi-round research agent: query decomposition, parallel web search, cross-source verification, structured report generation; configurable depth/breadth | OpenAI Deep Research, Google Deep Research, openJiuwen (deep research) |

### Enterprise Enhancement

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 10.3b | SCIM Directory Sync ✅ | Multi-Tenant & Enterprise | User/group auto-provisioning via SCIM 2.0 protocol. | SCIM RFC 7643/7644, Azure AD SCIM, Okta SCIM |

### Internationalization Enhancement

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 15.2 | Community Translations | Internationalization | Community-contributed translation file management: upload/review/version locale `.po`/`.json` files, locale coverage dashboard, missing-key detection. Built on i18n SPI (15.1) — translation files are pluggable assets, not Core code. | Django Rosetta, Crowdin |

### AIP Enterprise Capabilities (AgentArts[formerly Versatile]-Inspired)

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 6.23 | 5-Level Intent Recognition ✅ | Agent Intelligence |Shipped in `intent-recognition` (merges 1.| AgentArts AgentStudio (formerly Versatile) |
| 6.49 | Intent Package Asset ✅ | Agent Intelligence | Intent packages as manageable data assets, paired with 6. | AgentArts (意图包/意图管理), 7.2 evaluation dataset machinery ✅ |
| 6.24 | Object Log & Decision Log | Observability & Operations | Record object event snapshots and decision behavior snapshots, unified audit center, object history and time-series analysis | AgentArts AgentBase (formerly Versatile) |
| 6.25 | Object History Analysis | Knowledge Base/RAG | View object evolution over time, support state replay, object timeline operations, and rollback analysis | AgentArts AgentBase (formerly Versatile) |
| 6.26 | Simulation Environment | Agent Intelligence | Ontology simulation environment isolation: simulation execution and reasoning without affecting real systems. Visual reasoning analysis, one-click process deployment. **Planned enhancement (E5)**: Checkpoint Branching for What-If Analysis — create new execution sessions from historical checkpoints with modified state, enabling parallel "what-if" scenario testing without affecting the original session. | AgentArts AgentBase (formerly Versatile), Palantir Scenario Staging, LangGraph (checkpoint branching) |
| 6.27a | Computer-use (NEW — split from 6.27) | Tools & Plugins | Agent operates computer GUI to execute tasks: open native apps, click through UI, verify changes (Claude Code computer-use research preview pattern). Browser half of the original 6.27 moved to P3 as Browser Automation Tool (6.27, Playwright-based). | Claude Code (computer use from terminal), Manus |
| 5.5d ✅ | Dual-Format Plugin Convergence & Export | Tools & Plugins | One Agent Plugins 1.0 package is simultaneously a conformant open-standard plugin (skills/mcp. archive `openspec/changes/archive/2026-09-19-dual-format-plugin-convergence`. | Agent Plugins 1.0 spec §Client Extensions (§5.6 extensions + §8.2 extension directories), npm/OCI "interchange envelope" pattern, Claude Code marketplace (git-repo format) |
| 6.28 | DataAgent (NL2SQL) | Tools & Plugins | NL2SQL data query, data analysis script execution, chart generation, multi-data-warehouse tool integration | AgentArts AgentCapability (formerly Versatile) |
| 6.29 | VibeCoding Tool Set | Tools & Plugins | Claude Code-like CLI tool set: file IO, command execution, code execution, task management, file search | AgentArts AgentCapability (formerly Versatile) |
| 6.30 | Fine-Grained Permission Control | Security & Compliance | Object-level, attribute-level, row-level permission control, security policy fast mapping to business ontology orchestration engine | AgentArts AgentBase (formerly Versatile), Palantir Dynamic Security |
| 6.31 | Data Integration Framework | Deployment & Operations | Enterprise data source connectors (ERP/CRM/database), data pipeline orchestration, ETL/ELT support | AgentArts industrial integration (formerly misattributed "Versatile ET/OT Engine"), Palantir Foundry |
| 13.19 | Service Mesh Integration | Deployment & Operations | Istio or Linkerd service mesh for Hecate multi-replica K8s deployment. Provides: (1) mTLS between Hecate pods, Redis, PostgreSQL, MCP gateways — eliminates manual cert management; (2) east-west traffic policy (e.g., restrict tool-executor pods from calling external APIs directly); (3) observability — distributed tracing spans across pod boundaries (complements Hecate's existing OTel instrumentation with mesh-level traces); (4) advanced traffic management — canary routing for 13.1a Canary Release, shadow traffic for 6.8a A/B Testing, circuit breaking for 6.8c Per-Prefix Circuit Breaker. **Helm chart enhancement**: optional `serviceMesh.enabled` flag in `values.yaml` injects sidecar + creates DestinationRule/VirtualService resources. **Depends on**: 13.1 SaaS Deployment (production Helm chart), 13.4 K8s Scaling Test Harness (P4, not started — multi-replica baseline). | Istio (CNCF, market leader, ~75% production share), Linkerd (CNCF, lightweight, Rust-based), Consul Connect (HashiCorp), Cilium Service Mesh (eBPF-based, kernel-level observability) |
| 6.32 | gVisor Enhanced Sandbox | Deployment & Operations | Docker + gVisor user-space kernel (application-kernel syscall interception — a userspace sandbox, not a VM), ~200ms cold start, significant security improvement over pure Docker | E2B, OpenHands |
| 6.32a | Kata Containers Sandbox | Deployment & Operations | Docker-compatible VM-level isolation via Kata Containers runtime. | Kata Containers (OpenStack), AWS Firecracker, Google gVisor |
| 6.33 | Decision Simulation | Agent Intelligence | Pre-execution simulation of Action side effects, verify safety, reasoning and optimization in simulation environment without affecting real system | AgentArts Simulation Execution (formerly Versatile) |
| 6.40 | Firecracker microVM Backend | Deployment & Operations |Firecracker microVM as a fourth selectable AgentEnvironment backend (alongside local/docker/gVisor/Kata — chosen per threat model, not an upgrade chain).| AWS Lambda MicroVM (agent code execution foundation since 2018), 华为 AgentArts (microVM-based 安全沙箱), kubernetes-sigs/agent-sandbox (Kata+Firecracker integration) |
| 13.4c | Session-Level microVM Isolation (Paradigm B) | Deployment & Operations | **Paradigm shift** from Hecate's current model (Paradigm A: multi-replica pods + shared Redis/PG state + distributed locks) to Bedrock AgentCore model (Paradigm B: per-session Firecracker microVM + managed Memory service). Each chat session runs in its own microVM with isolated CPU/memory/filesystem — **eliminates dual-write race in steady state** (single writer per session; ownership handoff windows — 8h rebuild, failover — still need lease + fencing semantics), **eliminates Redis SETNX lock complexity**, **does not by itself solve connection pressure** (N sessions holding N direct PG connections bypass the pool but raise the backend total — Bedrock mitigates this via its managed Memory service, which is why 13.4c requires one). Trade-offs: resource overhead (Firecracker VMM itself ~5MiB + ~125ms cold start; a session running the Python agent runtime costs orders of magnitude more and must be measured, not extrapolated from the VMM figure), 8h max lifetime (a Bedrock AgentCore product policy, not a Firecracker constraint — self-hosted chooses its own session rebuild cadence), requires Memory service for cross-session persistence. **Architectural impact**: PregelRuntime needs refactoring from in-process execution to RPC-based microVM invocation; SessionStateStore becomes optional (state lives in microVM). **Distinct from 6.40** (which is sandbox-level microVM as AgentEnvironment backend): 13.4c is session-level isolation of the *entire agent runtime*, not just tool execution. **Trigger condition**: when noisy-neighbor problems emerge in production or enterprise customers demand Bedrock-equivalent isolation guarantees. **Depends on**: 6.40 Firecracker microVM Backend (P5, not started), PregelRuntime RPC refactor (new engine change), 13.4 K8s Scaling Test Harness (P4, not started — baseline for comparison). | Amazon Bedrock AgentCore (only production implementation of Paradigm B, per-session microVM + Memory service + 8h maxLifetime), Hecate 13.4a (Paradigm A baseline with Redis SETNX + PG FOR UPDATE), AgentScope 2.0 DistributedBackend (Paradigm A abstraction) |
| 6.41 | WASM Runtime Backend | Deployment & Operations |WebAssembly (WASI) as code execution backend for bounded compute and MCP tool components.| Microsoft Wassette (MCP-native Wasm runtime, Aug 2025), Cloudflare V8 Isolates (sub-ms startup), Extism/Wasmtime (WASI sandbox), kubernetes-sigs/agent-sandbox (WASM backend option) |
| 11.18 | Multi-Stream Modes | Access Channel | Support multiple stream output modes: values (full state after each superstep), updates (incremental diffs), messages (LLM token stream), debug (node events, channel changes). Clients can subscribe to multiple modes simultaneously. | LangGraph (4 stream modes: values, updates, messages, debug) |

### Re-scope Additions

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 8.21 | Projection Registry (NEW) | Observability & Operations | Generalized projection registry: any user-facing concept (permission presets, agent profiles, derived views) = named projection over the event log + canonical setter events; zero-database state derivation with consistent rebuild semantics. Permission presets become projections instead of DB tables. **Depends on**: 1.3.19 Event-Sourced Execution State (P3). **Seam notes**: `derive_messages()` 为第一个投影函数；注册表是命名投影的泛化，fold 机器不动。 | dsh projection registry (Part 4 Mech 18, source-verified) |
| 13.20 | Atomic File Writes & Cross-Process File Locking (NEW) | Deployment & Operations | `<file>.lock` advisory locking + write-to-temp + atomic rename commit for multi-process file mutation (skills, plugin configs, state files); stale-lock recovery; ~150 LOC utility. Hard requirement once multi-replica pods mutate shared filesystem assets. | dsh storage layer (Part 8 Mech 81, source-verified) |
| 11.11 | Voice Agent Pipeline (moved from P5) | Multi-Channel Access | End-to-end voice pipeline: STT → agent workflow → TTS with configurable STT/TTS providers. Barge-in support (interrupt TTS playback when user speaks). Voice is becoming a standard channel (OpenAI/Hermes/Salesforce/IBM all ship it). | OpenAI Agents SDK (VoicePipeline + RealtimeAgent), Hermes (streaming TTS + barge-in + wake words), Salesforce Agentforce Voice, IBM ElevenLabs managed TTS |
| 1.3.21 ✅ (2026-09-09) | Engine Parity: Dynamic Fan-out, Time-Travel Resume, Declarative Interrupts (NEW — 2026-09-07 deer-flow/LangGraph engine comparison) | Agent Runtime | Close the three engine-level gaps found by diffing Hecate's Pregel against LangGraph (deer-flow's inherited engine; source-verified). **Shipped 2026-09-09** archive `openspec/changes/archive/2026-09-08-declarative-interrupts`, `openspec/changes/archive/2026-09-09-node-cache-policy`, `openspec/changes/archive/2026-09-09-time-travel-resume`, `openspec/changes/archive/2026-09-09-dynamic-fanout`. | LangGraph Send / Command / interrupt / get_state_history / update_state / CachePolicy (docs.langchain.com, source-verified), deer-flow v1 sequential research loop + v2 lead-agent retreat (source-verified), Hecate engine audit 2026-09-07  **Deferred** (防遗忘): provider prefix-cache usage passthrough, distributed cache backend, single_flight, large-payload offloader, fork cache inheritance; FORK payload compression, dangling-turn sweep, commit-point enrichment, fork quotas; platform-quota linkage for FanoutLimitError. |

### P4 Dependency Chain

```
SDK/CLI → Code Sandbox → Managed Runtime → NL2Agent → NL2Workflow
Quality Signals + Evaluation → Self-Evolution Skill Loop (1.3.6f ✅) → Learned Skills → Agent Quality Flywheel
Hallucination Detection → Self-Evolution Skill Loop (1.3.6f) → Intent Recognition → Deep Research
Evaluation → Agentic RL Framework (6.15) → Data Flywheel → Model Optimization
Evaluation → Prompt Self-Optimization (6.19 ✅) → ACE/GEPA Algorithm → Auto-Optimized Prompts
Knowledge Graph (3.5.1, P5 deferred) → Ontology Action System (6.20) → Object Actions → Writeback
6.20 + RAG → OAG (6.22) → RAG + Logic + Actions Closed Loop
A2A Protocol (P3) → Peer Selection → Agent Team Templates → Distributed Team Orchestration
A2A Protocol (2.10 ✅) → ACP Support (2.13) → external coding agents as worker nodes
Event-Sourced State (1.3.19, P3) → Projection Registry (8.21) → derived state without DB
Multi-Agent → Expert Panel → Inter-Agent Comm → Central Controller
Central Controller → Controller Canvas → Orchestration Mode → Execution Visualization
Advanced RAG → Multi-Modal → GraphRAG → Reranking (Extended Document 3.1.8 deferred P5, 2026-08-14)
Knowledge Graph (3.5.1-3.5.3, P5 deferred) → GraphRAG Query Engine (3.5.4) → DRIFT Search (KM4) + Schema-Aware Traversal (KM5) → Lazy GraphRAG (3.5.14/KM2)
Memory Integration → Sleep-time Consolidation (KM3 ✅ via 4.5, 2026-09-20) → Overnight Synthesis → Learned Context
Task Memory (4.21) → Work Context Graph (KM6) → Self-Improving Work Memory
Agent-Native Graph Memory (3.5.6) → Temporal Memory & Reasoning (3.5.13/KM1) → Time-Aware Retrieval
Tool Enhancement → MCP Server → MCP Sandbox Security
Deterministic Hooks (1.3.5i) → Session Events + Tool Matchers (TP4) → Per-Tool Hook Configuration
Agentic RAG (3.2.10) → Iterative Retrieval → Query Reformulation → Multi-Step Reasoning
GraphRAG Query Engine (3.5.4) → Global/Local/Hybrid Search → Multi-Granularity Retrieval
Knowledge Graph API (3.5.5, P5 deferred) → CRUD + Cypher Queries → Text-to-Cypher
Agent-Native Graph Memory (3.5.6) → Graph-Aware Context Assembly → Persistent Graph Memory
Conversation Recall Storage (4.18 ✅) → Semantic Search over History → Long-Term Context
Self-Editing Memory (4.19 ✅) → LLM-Driven Memory Correction (revision + audit) → Memory Quality Improvement
Retrieval Escalation (4.20 ✅, rescoped from Function Chaining/heartbeat) → Weak-Search Hint Gating → Complex Query Resolution (v2: gated recall sub-agent)
Tool Memory (4.22) → Tool Usage Learning → Parameter Tuning
Cross-Thread Memory Store (4.23) → Cross-Session Facts → Shared Knowledge
LLM → 5-Level Intent Recognition (6.23 ✅) → Controller Self-Evolution (data path ✅, auto-tuning follow-up) → Intent Caching (✅)
EventStore → Object Log & Decision Log (6.24) → Object History Analysis (6.25) → State Replay
Ontology Action System (6.20) → Decision Simulation (6.33) → Simulation Environment (6.26) → Safe Verification
LLM → Browser Automation (6.27, P3) → Computer-use (6.27a, P4) → GUI Automation
RAG + LLM → DataAgent (6.28) → NL2SQL + Data Analysis + Chart Generation
CLI Tools → VibeCoding (6.29) → File IO + Command Execution + Code Execution
Pregel Runtime → Multi-Stream Modes (11.18) → values/updates/messages/debug
Engine Parity (1.3.21): Declarative Interrupts (① ✅ 2026-09-08) → Time-Travel Resume (② ✅ 2026-09-09) → Dynamic Fan-out (③ ✅ 2026-09-09) → Deep Research (5.11) parallel collection; ② feeds What-If Checkpoint Branching (6.26 E5) + executable 8.20 replay; ③ unlocks map-reduce aggregation + 5.11 parallel collection, T2b branch-output invariant closes the FAN_OUT log/fork gap
Streaming → Asynchronous Execution API (1.3.11) → Long-Running Workflow Support
AuthProvider (P3) → SCIM Directory Sync (10.3b)
i18n SPI (P3) → Community Translations (15.2)
```

---

## P5: Ecosystem & Industry (Months 13-15)

> Industry capabilities, marketplace, compliance, desktop/mobile clients, distribution, end-user applications, template ecosystem.

### Asset Marketplace

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 12.0 | Asset Marketplace | Industry Capabilities | Unified asset discovery/subscription/installation entry, covering browse→try→install→configure workflow for six asset types: app templates, models, MCP, plugins, Prompts, Skills. **Planned enhancement (EC3)**: Semantic Marketplace Discovery — vector search by intent: user/agent describes what they need, semantic similarity matches best agent/tool/skill. Failed searches become demand signals (bounties) for supply side. **Planned enhancement (EC6)**: Governed Agent Catalog — agent listing requires governance approval workflow (submit → security scan → evaluation → approval → publish). Supports agents built with any framework. Cross-cloud, cross-system catalog with publisher identity verification and lifecycle governance. **Rescoped (2026-08-16)**: v1 = Agent Plugins installer (5.5c) + static git-index directory (Claude Code marketplace / Codex Plugins Directory pattern) + scan-results display (5.13a); **E-tier only** — indexes T4 declarative packages and T2 MCP registrations, never code plugins (ADR-029: "Marketplace = K-C, goods are E"). Full store semantics deferred until T4 supply evidence exists (GPT Store decline: stores without supply fail; ClawHavoc: stores without governance fail). EC3/EC6 enhancements frozen pending the same evidence. | AgentArts (asset marketplace), Coze (plugin marketplace), OpenClaw (ClawHub), Google (Agent Finder), IBM (Agent Catalog, governed, any framework) |

### Industry Capabilities

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 12.1 | Industry Templates | Industry Capabilities | Pre-built industry Agent templates (finance, healthcare, legal, education…) | AgentArts |
| 12.2 | Industry Knowledge Packs | Industry Capabilities | Pre-built industry knowledge bases (regulations, terminology, workflows…) | AgentArts |
| 12.3 | Industry Skill Packs | Industry Capabilities | Pre-built industry skills (report generation, data analysis, compliance checks…) | AgentArts (Skill system) |
| 12.4 | Industry Integration Guide | Industry Capabilities | Vertical industry quick-start guide and best practices | AgentArts (best practices) |
| 12.5 | Partner Monetization Infrastructure | Industry Capabilities | Partner program infrastructure for commercial ecosystem: (1) **Stripe payment integration** — in-app purchasing with credit card processing, invoicing, and automated payouts; (2) **Revenue sharing engine** — configurable split (e.g., 70/30) between platform and partner, automatic calculation per transaction; (3) **Unified billing** — consolidate all marketplace asset purchases (agents, tools, MCP servers, skills) into a single customer bill; (4) **Auto-provisioning** — purchased assets activated instantly, no manual setup; (5) **Partner GTM console** — product management, offer creation, invoice tracking, payout dashboard. Enables ISV partners to build, distribute, and monetize on Hecate marketplace. **Frozen**: deferred until 5.5c-driven T4 content supply and 12.0 v1 traction evidence exist. | Salesforce AgentExchange ($800M ARR, GTM app + Stripe + unified billing), IBM Agent Connect (ISV onboarding + sales channels), Huawei AI Model Partner Program (20+ model providers) |

### Template & Import Ecosystem

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 1.1.6 | Template Marketplace | Agent Development | Pre-built Agent and workflow templates for quick start. **Planned enhancement (G8)**: Component Marketplace — community-contributed individual drag-and-drop components (not full templates) such as "PDF Summarizer", "Web Scraper", "Chart Generator" as pre-configured Tool+LLM combos. Granular reuse at node level, complementing app-level template sharing. | Coze (30+ templates), Langflow (100+ components) |
| 5.10 | Prompt Template Marketplace | Tools & Plugins | Browse, search, copy, save, batch import/export, AI optimization of Prompt templates | AgentArts (Prompt marketplace), LangFuse (Prompt Management) |
| 1.1.12 | Dify Workflow Import | Agent Development | Import Dify DSL workflow files, auto-convert to Hecate format | AgentArts (Dify import compatibility) |

### Compliance

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 9.6 | Compliance Framework | Security & Compliance | SOC2, GDPR, MLPS compliance support | Enterprise standard |
| 9.6a | EU AI Act Compliance | Security & Compliance | Risk assessment for high-risk AI systems, transparency logging (decision explanations), human oversight documentation, conformity assessment reports. Covers Articles 9 (risk management), 13 (transparency), 14 (human oversight). | EU AI Act (2026 enforcement), IBM (AI governance), Salesforce (trusted AI) |
| 9.9 | Compliance & Audit Center | Security & Compliance | Centralized compliance dashboard: compliance posture score, policy management UI (create/view/edit audit policies), automated compliance scanning with violation reports, regulatory reporting templates (SOC2, GDPR, EU AI Act, MLPS). Audit log viewer with advanced filtering, export, and retention management. Integrates with Compliance Checker Agent (13.9d) and Decision Lineage (6.21) for full audit trail. | Microsoft Purview, Palantir governance controls, AgentArts (content moderation) |
| 6.46 | Model Governance | Model Management | Model approval workflows for deployment (propose → review → approve → deploy), risk scoring per model (bias, fairness, reliability metrics), automated compliance reporting for AI models, model audit trail with full deployment history. Integrates with Compliance & Audit Center (9.9) for unified policy enforcement and with Model Lifecycle Manager (6.45) for gated promotion. | IBM watsonx.governance (risk management, bias detection), Salesforce trust layer, Palantir governance controls |

### Knowledge Graph Visualization

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 3.5.7 | Knowledge Graph Visualization | Knowledge Base/RAG | Interactive graph browser: node expand/collapse, relationship filtering, community highlighting, path search. Visual exploration of knowledge structure. | Neo4j Bloom, D3.js force graph |
| 3.5.8 | Knowledge Graph UI Editor | Knowledge Base/RAG | Visual graph maintenance UI: edit entities/relationships, schema management, bulk import/export, graph quality detection. | Dify workflow builder (graph section) |

### Ontology Modeling

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 3.5.9 | Ontology Schema Definition | Knowledge Base/RAG | Define class hierarchies (inheritance), property constraints (domain/range/cardinality), relationship types. JSON Schema format definition, aligned with 3.5.1 Knowledge Graph Construction schema-constrained extraction. | @ontograph/core, UOD |
| 3.5.10 | SHACL Validation | Knowledge Base/RAG | W3C SHACL standard-based graph data quality validation: verify entities/relationships conform to ontology-defined constraints. **Planned enhancement (KM5)**: Schema constraints integrated into GraphRAG traversal — SHACL shapes act as traversal guards during multi-hop reasoning, ensuring retrieved subgraphs are structurally valid per enterprise ontology before semantic scoring. | Neo4j neosemantics, W3C SHACL, SCAIR (schema-gated retrieval) |
| 3.5.11 | Ontology Import/Export | Knowledge Base/RAG | Support OWL 2/RDF/JSON-LD format ontology import/export for cross-system ontology interoperability. | Neo4j neosemantics, @ontograph/core |
| 3.5.12 | Ontology Versioning | Knowledge Base/RAG | Ontology version management: version snapshots, version diff, backward compatibility checks. Aligned with Resource Versioning (14.x) mechanism (see 1.3.20 carrier). | @ontograph-core (version management) |
| 8.1d | W3C Trace Context Propagation | Observability & Operations | Cross-agent trace context propagation via A2A protocol headers. Enables end-to-end observability in multi-agent workflows spanning different frameworks/organizations. Uses W3C Trace Context standard with OpenTelemetry. **Planned enhancement (OE6)**: Multi-Agent Distributed Tracing — end-to-end tracing across A2A agent calls with sub-agent execution visualization, cross-organization trace correlation, and agent-to-agent latency breakdown in trace timeline. | A2A v1.0 (2026), OpenTelemetry, W3C Trace Context, OpenTelemetry GenAI Agent Spans |
| 7.8a | Agent Benchmark Integration | Evaluation & Testing | Integration with industry-standard agent benchmarks: SWE-bench (software engineering), AgentBench (general), τ-bench (tool use). Automated evaluation against standardized tasks. | SWE-bench (Princeton), AgentBench (THUDM), LangGraph (benchmark integration) |

### Memory Management

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 4.24 ✅ (2026-09-22) | Memory Versioning | Memory System | monotonic `revision` counter on every L1 / L3 / L4 row (incremented on every successful mutation); `memory_update` / `memory_forget` accept optional `expected_revision` — mismatch returns structured revision-conflict error carrying the current revision. **Delivered 2026-09-22**.| Mem0 (revision+supersede lineage), Letta (version history) |

### Multi-Modal & Desktop

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 11.11a | Vision Input (Multi-Modal) | Multi-Channel Access | Image understanding for screenshots, document OCR, UI analysis, chart interpretation. First step toward multi-modal agent. Leverages LLM vision capabilities (GPT-4o, Claude 3.5 Sonnet). | Google ADK (multi-modal), Salesforce (visual grounding), OpenClaw (image tools) |
| 11.12 | Desktop Client | Multi-Channel Access | Windows/macOS native client, local file processing, Office document operations | AgentArts (desktop client) |
| 11.13 | AgentSpace SDK (Embedded Integration) | Multi-Channel Access | Layered SDK from lightweight UI components to deep integration APIs, enabling rapid embedding of Agent capabilities in existing enterprise applications. **Planned enhancement (EC5)**: Cross-Surface Experience Layer — define agent behavior and interactive UI components once, deploy natively across all channels (web, mobile, Slack, Teams, voice, ChatGPT-compatible surfaces). Separates agent logic from delivery surface, eliminating per-channel UI/security/data rebuild. Includes adaptive rendering: agent responses adapt to surface constraints (voice = TTS, mobile = cards, web = rich UI). Follows Salesforce AXL Headless Experience Layer pattern. **EC5 frozen (2026-08-16)**: deferred until T4 supply evidence exists. | AgentArts AgentSpace (formerly Versatile) (SDK integration), Salesforce AXL (define once, deploy everywhere) |

### Distribution & End-User Access

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 13.14 | PyPI Package Distribution | Deployment & Operations | pip-installable SDK (hecate-sdk) published to PyPI; programmatic Agent creation, workflow definition, and execution without UI; CLI tools for agent management. **Planned enhancement (EC4)**: Community Agent Gallery — community members publish `agent.json` (agent config + system prompt + examples) to a shared gallery. Other users one-click install/try/fork. Public Trace Gallery shares interesting execution trajectories. Agent Harness Registry lists compatible tools and runtimes. Follows Hugging Face tiny-agents pattern. **EC4 frozen (2026-08-16)**: deferred until T4 supply evidence exists. | openJiuwen (PyPI: openjiuwen), CrewAI (pip install crewai), Hugging Face (tiny-agents collection + `agent.json`) |
| 13.16 | Edge/Lite Deployment | Deployment & Operations | Lightweight single-binary deployment option for edge/on-device scenarios. Minimal dependencies (SQLite + local embedding). Enables running agents on developer laptops, IoT devices, or air-gapped environments. **Planned enhancement (EF6)**: Confidential Computing Mode — HYOK (Hold Your Own Key) encryption with customer-managed keys, data capsules for process-level isolation, confidential inference via local models (Ollama sidecar) with zero outbound data flow. Air-gapped mode flips `AIRGAPPED=true` refusing all outbound calls. For defense, healthcare, and financial high-compliance scenarios. | Claude Code (local), Cloudflare Agents (edge), Vercel AI SDK, Huawei AgentArts (HYOK + data capsules), AiSOC (air-gapped + Ollama) |
| 2.12 | Agent Payments (AP2) | Multi-Agent Orchestration | Google Agent Payments Protocol (AP2) for agent-to-agent financial transactions. Extension of A2A protocol with payment primitives. Enables autonomous commerce between agents with human consent gates. | Google + Coinbase (Sep 2025), A2A extension |
| 11.14 | End-User Application | Multi-Channel Access | User-facing web application (agent-studio style) with conversation interface, agent discovery, knowledge base browsing, and session management; separate from admin/developer console | openJiuwen (agent-studio), Coze (user app) |
| 11.15 | Mobile GUI | Multi-Channel Access | Mobile-responsive admin/chat interface for agent management and conversation on iOS/Android; supports push notifications and offline message queuing | openJiuwen (mobile GUI), Coze (mobile) |
| 11.6 | WeChat Official Account / Mini Program | Multi-Channel Access | WeChat ecosystem integration (订阅号 / 服务号 / Mini Program). **Status (2026-08-12, P5 deferred)**: to-C 场景，不是 Hecate to-B 主线。Trigger = 第一个 to-C / 微信公众号场景的明确客户需求。S. | Coze (WeChat channel) |
| 11.10 | Custom Channel SDK | Multi-Channel Access | SDK-extensible custom channels（让用户自建渠道适配器）。**Status (2026-08-12, P5 deferred)**: 长尾 niche，trigger = 社区或客户明确请求"自建渠道"。ChannelBase SPI 在 Wave 1/2 实战后沉淀的稳定接口再开放给外部，避免 SPI 频繁变动。 | OpenClaw (channel plugin architecture) |
| 11.2 (full) | Web Widget (Full — anonymous to-C) | Multi-Channel Access | Full version of Web Widget for anonymous公开场景。**Status (2026-08-12, P5 deferred)**: WidgetModel + 临时 JWT 签发 + Origin 白名单 + JWT RS256 + JS bundle（参考 Intercom bundle 优化、Salesforce RS256、IBM watsonx 双 RSA 密钥、Google CX Agent Studio token broker 模式、Dialogflow Messenger Web Component 形态）。**Trigger = 第一个 to-C 公开网站场景（自部署用户做营销 / 客服 / Lead 收集）的明确客户需求**——11.2 简化版（P3 Wave 1）只覆盖内部 Portal / embeddable，不覆盖公开匿名。架构复用：浏览器直接调 `/v1/chat/completions`（不走 ChannelBase）。 | Intercom (JWT + bundle optimization), Salesforce Enhanced Web Chat (RS256 + identityToken API), IBM watsonx (双 RSA 密钥对 + 加密 payload), Google CX Agent Studio (token broker + reCAPTCHA), Dialogflow Messenger (Web Component) |
| 11.4 | WeCom (WeChat Work) | Multi-Channel Access | WeCom bot SDK integration — Wave 2 中国 IM。复用 11.3 飞书模式（webhook + signature verification + 企业 IM 消息模板）。**Moved to P5 (2026-08-22 release reclassification; originally P5-deferred 2026-08-12)**。Trigger = 第一个明确要企微的中国客户需求。 | OpenClaw (WeCom channel) |
| 11.5 | DingTalk | Multi-Channel Access | DingTalk bot SDK integration — Wave 2 中国 IM。复用 11.3 模式。**Moved to P5 (2026-08-22 release reclassification; originally P5-deferred 2026-08-12)**。Trigger = 第一个明确要钉钉的中国客户需求。 | OpenClaw (DingTalk channel) |
| 11.9 (D/T) | Discord / Telegram | Multi-Channel Access | International channels (Wave 2)。各 S，复用 11.9 Slack ChannelBase 模式。**Split from 11.9 and moved to P5 (2026-08-22 release reclassification; originally P5-deferred 2026-08-12)**。Trigger = 第一个明确要 Discord/Telegram 的国际客户需求。 | OpenClaw (20+ channels), Hermes Agent (multi-platform gateway pattern) |

### AIP Advanced Capabilities (AgentArts[formerly Versatile]/Palantir-Inspired)

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 6.34 | AI Office | Tools & Plugins | AI generation and processing of PPT, Word, Excel documents with template parsing | AgentArts AgentCapability (formerly Versatile) |
| 6.35 | Industrial Data Integration | Deployment & Operations | MQTT + OPC UA protocol access, OT data fusion engine for industrial IoT data *(Reference note 2026-08-14: previously attributed to "Versatile Industrial Data Graph Platform" — "Versatile" was Huawei's agent platform renamed AgentArts (智果) in Feb 2026, which has no such named module; reference corrected to generic industrial-IoT practice)* | MQTT/OPC UA ecosystems, industrial IoT platforms |
| 6.36 | Asset Marketplace Operations | Industry Capabilities | Agent/MCP/plugin asset lifecycle: publish → use → precipitate. Asset operation incentive mechanism, partner monetization via cloud store | AgentArts Asset Marketplace (formerly Versatile AgentGallery) |
| 5.13 | Plugin Security & Signing | Tools & Plugins | End-to-end plugin security lifecycle for marketplace distribution: (1) **Plugin signing** — cryptographic signing with Ed25519 keys, signed manifest in `plugin.yaml`, key registry for publisher identity verification; (2) ~~**Security scanning**~~ **(split out 2026-08-16)** — content scanning extracted to **5.13a (P3)** as precondition for 5.5c; this feature retains scanning only for code plugins at marketplace publication; (3) **Digest verification** — SHA-256 digest verification on install, tamper detection, rollback on verification failure; (4) **Security score** — 0-100 rating per plugin based on scan results, displayed in marketplace, configurable minimum score threshold for organizational install policy. Integrates with Asset Marketplace (12.0) and Compliance & Audit Center (9.9). **Rescoped (2026-08-16)**: signing-first remainder stays P5 bound to 12.0 — signature proves identity, not behavior; for pure-data T4 goods its value is lower than 5.13a content scanning. | OpenClaw (ClawPack signed manifest + digest verification), ClawHub (security scan summaries, SkillScan), npm (package signing), PyPI (signed packages) |
| 6.37 | Memory Clustering & Conflict Resolution | Memory System | Memory clustering and graph-structured generation, memory conflict processing, traceability to source messages | AgentArts Memory Engine (formerly Versatile) |
| 6.38 | Self-Planning (PDDL + MCTS) | Agent Intelligence | PDDL formal expression + Monte Carlo search tree + Tree/Graph of Thought, ultra-long task planning with 100+ execution steps | AgentArts Agent Self-Optimization (formerly Versatile) |
| 6.39 | Tool Auto-Creation | Tools & Plugins | Agent automatically creates new tools based on task requirements without human intervention | AgentArts Agent Self-Optimization (formerly Versatile) |
| 6.41b | Cloud Document Connector | Knowledge Base/RAG | Block structuring, custom templates, AI preprocessing, data/relationship/summary extraction from cloud documents | AgentArts AgentBase (formerly Versatile) |
| 6.42 | Global Branching | Knowledge Base/RAG | Ontology zero-downtime evolution, branch-isolated development and testing | Palantir Global Branching |
| 6.43 | Embedded Ontology | Deployment & Operations | Lightweight ontology for edge devices, supporting offline decision-making | AgentArts Edge Deployment (formerly Versatile), Palantir Embedded Ontology |
| 11.19 | Platform-Level Governance | Access Channel | Unified enforcement layer for identity + data + API + AI trust policies. 20+ governance policies including auto API key rotation, JWT auth, real-time sensitive content blocking, model routing, fallback. | Salesforce (MuleSoft Flex Gateway), Palantir (Trust Layer) |
| 11.20 | Zero Trust Architecture | Access Channel | IAM-based service accounts with principle of least privilege. Token exchange for identity propagation (OAuth 2.0 Token Exchange RFC 8693). Per-agent unique identity with scoped permissions. | Google ADK (IAM + service accounts), Salesforce (Zero Trust) |
| 14.1 | Agentic Resource Discovery (ARD) | Ecosystem | Support for the Agentic Resource Discovery open specification (Google + Microsoft + Hugging Face, June 2026): (1) **Catalog publishing** — publish `ai-catalog.json` on Hecate's domain (like `robots.txt` for agents), listing all discoverable agents, skills, MCP servers, and tools; (2) **Federated registry integration** — Hecate catalogs are crawlable by external registries (Google Agent Registry, Hugging Face Discover), and Hecate can crawl/index external catalogs; (3) **Runtime capability discovery** — agents discover external capabilities at runtime by querying ARD registries with intent descriptions, receiving verified publisher metadata; (4) **Trust verification** — publisher identity verified via domain ownership and cryptographic signatures before establishing direct connection. Complements A2A AgentCard (per-agent discovery) with catalog-level standard. **Frozen**: deferred with the EC ecosystem suite until T4 supply evidence exists. | Google ARD spec + Agent Registry, Hugging Face Discover Tool (reference implementation), Microsoft, broad industry participation |

### Deferred from P3/P4 (competitor-analysis re-scope)

> Trigger-based — no scheduled delivery. Rationale and evidence: `docs/research/2026-08-competitor-analysis.md` §Feature decisions. Rows moved verbatim from their original sections.

| # | Feature | Domain | Description (original) + P5 trigger | References |
|---|---------|--------|--------------------------------------|------------|
| 3.1.2 | OCR | Knowledge Base/RAG | Image text recognition. **Trigger**: customer demand — integrate Docling/Unstructured instead of building. | RAGFlow (Tesseract/RapidOCR) |
| 3.1.3 | Table Extraction | Knowledge Base/RAG | Structured table recognition and extraction. **Trigger**: same as 3.1.2. | RAGFlow (table structure recognition) |
| 3.1.4 | Layout Analysis | Knowledge Base/RAG | Document layout structure recognition (headings, paragraphs, charts). **Trigger**: same as 3.1.2. | RAGFlow (10 layout types) |
| 3.4.2 | High-Throughput Retrieval | Knowledge Base/RAG | Low-latency vector retrieval under high concurrency. **Trigger**: Qdrant native sharding + deployment guide covers it until real bottleneck appears. | Qdrant (sharding + replication) |
| 3.5.1 | Knowledge Graph Construction | Knowledge Base/RAG | LLM-based entity/relationship extraction pipeline: document → TextUnit → entity → relationship → merge & dedup; schema-constrained extraction. **Trigger**: integrate GraphRAG/LlamaIndex when a KG use case lands. | Microsoft GraphRAG, LlamaIndex SchemaLLMPathExtractor |
| 3.5.2 | Graph Database Integration | Knowledge Base/RAG | Graph database backend abstraction (Neo4j production / in-memory dev) via GraphStore ABC. **Trigger**: same as 3.5.1. | LlamaIndex Neo4jPropertyGraphStore |
| 3.5.3 | Community Detection & Summarization | Knowledge Base/RAG | Leiden hierarchical community detection + bottom-up summary generation. **Trigger**: same as 3.5.1. | Microsoft GraphRAG (Leiden) |
| 3.5.5 | Knowledge Graph API | Knowledge Base/RAG | Graph CRUD API: Cypher queries, template queries, Text-to-Cypher. **Trigger**: same as 3.5.1. | LlamaIndex TextToCypherRetriever, Neo4j API |
| 3.1.8 | Extended Document Processing | Knowledge Base/RAG | 20+ document formats incl. audio/video/structured/legacy; extensible parser registry. **Trigger**: same as 3.1.2. | Docling (20+ formats) |
| 6.9 | Provider Info Enhancement | Model Management | Provider bilingual name, icon upload, description, auth status indicator. **Trigger**: after 13.1 SaaS launch + real user feedback. | AgentArts (provider management) |
| 6.10 | Key Security Enhancement | Model Management | KMS encrypted key storage, rotation, masked display. **Trigger**: same as 6.9. | AgentArts (KMS encryption) |
| 6.12 | Provider Auth State Management | Model Management | Auth state lifecycle: unconfigured → configured → removed. **Trigger**: same as 6.9. | AgentArts (auth state management) |
| 6.13 | Model Management UI Redesign | Model Management | Three-level page structure, breadcrumb nav, dual-panel testing layout. **Trigger**: same as 6.9 (UI without users is speculative — Dify spent $30M on UI). *(List-level search/filter pulled forward as 6.48 — do not double-build here.)* | AgentArts (three-level nav) |
| 1.1.18 | Agent-Workflow Canvas Embedding | Agent Development | Drag Agent into Workflow canvas / Workflow as Tool node; recursive nesting. **Trigger**: user feedback; aim at Dify Loro CRDT collaborative editing direction. | Coze (Bot + Workflow), watsonx (Agent Node) |
| 1.1.19 | Unified Skill Selector | Agent Development | Unified picker for Tools/KBs/Workflows/sub-Agents. **Trigger**: same as 1.1.18. | Copilot Studio, Agentforce |
| 1.1.20 | Nested Graph Visualization | Agent Development | Expand/collapse sub-graphs in canvas. **Trigger**: same as 1.1.18. | ADK (subgraph), LangGraph (subgraph) |
| 6.21 | Decision Lineage | Observability & Operations | Record decision lineage: who made what decision based on what data version at what time; feedback learning + compliance auditing. Planned enhancements EF3 (Data Lineage Pipeline: full RAG provenance) + OE4 (Data-to-Decision Full-Chain Traceability). **Trigger**: minimal provenance (input source+version, tool results, policy version, approver, output references) needs no Ontology foundation — only the Palantir-standard full binding (data + function + app version per trace) does; effort underestimated initially. | Palantir Decision Lineage, Palantir AIP, AgentArts (fka Versatile), Salesforce Data 360 |

### P5 Dependency Chain

```
Asset Marketplace → Plugin Security & Signing (5.13) → Industry Capabilities → Compliance Certification
Asset Marketplace (12.0) → Semantic Discovery (EC3) + Governed Catalog (EC6) → Partner Monetization (12.5/EC2)
A2A Protocol (2.10) → Agentic Resource Discovery (14.1/EC1) → Federated Registry Integration
PyPI Distribution (13.14) → Community Agent Gallery (EC4) → Public Trace Gallery
AgentSpace SDK (11.13) → Cross-Surface Experience Layer (EC5) → Define Once, Deploy Everywhere
Template Marketplace → Dify Import → End-User Application
Desktop Client → Mobile GUI → Vision Input (Voice moved to P4 as 11.11, 2026-08-14)
PyPI Distribution → AgentSpace SDK → Enterprise Integration
Industrial Data Integration → Embedded Ontology → Edge Deployment
Ontology Action System (P3) → Decision Simulation (P4) → Global Branching (P5)
Memory Clustering → Conflict Resolution → Memory Traceability
6.41b Cloud Document Connector → Content Structuring → Knowledge Graph
```

---

## Reference Platforms

### Primary Reference Platforms (Enterprise)

These four Western enterprise platforms are the primary architectural references for Hecate's enterprise features. Consult them first when researching new capabilities.

| Platform | Vendor | Core Architecture | Key Innovation | When to Reference |
|----------|--------|-------------------|----------------|-------------------|
| **Microsoft Copilot Studio** | Microsoft | Generative Orchestration + Topics | LLM-driven planner automatically selects/composes Topics, Tools, Knowledge. Two orchestration modes (Generative vs Classic). | Agent orchestration patterns, Tool/Topic composition, multi-intent handling, Generative vs deterministic orchestration |
| **Salesforce Agentforce** | Salesforce | Hybrid Reasoning (Agent Script + Atlas Engine) | Explicit boundary between deterministic logic and LLM reasoning. `before_reasoning`/`after_reasoning` guarantees deterministic execution zones. `available when` conditions control Tool visibility to LLM. | Deterministic/LLM hybrid execution, guardrails, action chaining, subagent routing, enterprise auditability |
| **Google Vertex AI Agent Builder / ADK** | Google | LlmAgent + WorkflowAgent composition | Two-tier: LlmAgent (LLM reasoning) + WorkflowAgent (Sequential/Parallel/Loop — no LLM). ADK 2.0 adds graph-based WorkflowRuntime. A2A protocol for cross-framework agent communication. | Graph execution engine design, workflow agent patterns (sequential/parallel/loop), multi-agent hierarchies, A2A protocol |
| **IBM watsonx Orchestrate** | IBM | Agent + Agentic Workflow mutual nesting | Agent and Workflow are independent but composable: Agent calls Workflow as Tool, Workflow embeds Agent as Node. Recursive nesting with shared context. ReAct / Plan-Act agent styles. Enterprise control plane. | Agent-Workflow composability, enterprise governance, tool catalog management, multi-model routing, observability |

### Chinese / Open-Source Platforms (Secondary)

| Platform | Vendor | Core Architecture | When to Reference |
|----------|--------|-------------------|-------------------|
| **AgentArts** | Huawei | Single Agent / Workflow (Conversational vs Task) / Multi-Agent Controller | Three application modes, conversational vs task workflow distinction, multi-agent intent routing, NL2Workflow, evaluation system |
| **openJiuwen** | Huawei (Open Source) | Single Agent / Workflow / Multi-Agent | Open-source sibling of AgentArts (~90% similar). agent-core (Python SDK, OpenTelemetry observability, sandbox execution) + agent-studio (low-code visual platform). Same lineage as AgentArts — validates Hecate's graph-based approach against production-proven open-source. |
| **Coze (扣子)** | ByteDance | Bot (shell) + Workflow/Chatflow (skills) | Bot mounts Workflow/Chatflow as skills, Workflow ↔ Chatflow convertible, trigger/scheduler system |
| **Baidu Qianfan AppBuilder** | Baidu | Autonomous Agent / Workflow Agent / Multi-Agent Pro | Workflow Agent with dialog flow, global jump nodes, information collection nodes, agent nodes in workflows |
| **Alibaba Bailian** | Alibaba | Agent / Workflow / High-Code | Same canvas switches between dialog mode and workflow mode (conversation_id presence), simplest mode switching |
| **Dify** | Open Source | Chatflow / Workflow | Two workflow types sharing the same graph engine, conversational vs task execution model |
| **FastGPT** | Open Source | Everything is Flow | No mode distinction — simplest app = [Start] → [AI Chat], complex = multi-node DAG. Natural progressive complexity. |

### Frameworks (Developer)

| Framework | Architecture | When to Reference |
|-----------|-------------|-------------------|
| **LangGraph** | Everything is StateGraph | Graph execution engine design, channel/state management, streaming modes |
| **AutoGen** | Message passing + Team abstractions | Multi-agent team patterns (RoundRobin, Selector, Swarm), single-agent-to-team evolution |
| **CrewAI** | Agent + Crew + Process | Sequential/Hierarchical process models, task assignment |
| **Flowise/Langflow** | Visual flow builders | DAG-based visual editing, dependency-driven execution |

---

## Industry Architecture Trends (2026-06 Research)

Based on cross-platform analysis of 31 projects, four convergent trends emerged:

1. **Deterministic + LLM Hybrid Orchestration**: Enterprise platforms explicitly separate deterministic logic from LLM reasoning. Agentforce's `before_reasoning`/`after_reasoning`, Vertex AI's `WorkflowAgent` (no LLM), Copilot Studio's Generative vs Classic modes. LLM is called only where reasoning is genuinely needed — cost, latency, and auditability drive this.

2. **Agent + Workflow Composability**: Agent and Workflow are not mutually exclusive modes — they are composable building blocks. Agent can invoke Workflow as a Tool (Coze, watsonx). Workflow can embed Agent as a DAG node (watsonx, Agentforce). Both share Session/Memory/Context. Hecate's `_AgentWorker → WorkflowExecutionService` (P2) provides the engine-layer foundation; P3 adds service/API composition semantics.

3. **Graph/State Machine as Universal Execution Model**: All platforms converge on graph-based execution. Agentforce's Agent Graph (state machine), ADK 2.0's WorkflowRuntime (graph), watsonx's Agentic Workflow (DAG), Copilot Studio's Topic (conversation node graph). Hecate's PregelRuntime is aligned with this trend.

4. **LLM Called Only Where Necessary**: Enterprise platforms minimize LLM invocations. Agentforce has 8 explicit LLM call points; everything else is deterministic. Vertex AI's WorkflowAgent contains zero LLM. Hecate's `_LLMWorker` is the sole LLM call point — all other Workers (Condition, Tool, Variable, Knowledge, Suggestion) are deterministic.

---

## Dropped Features (competitor-analysis re-scope)

> Removed from the catalog — the industry has moved beyond these; Hecate self-building is negative ROI. Full rationale and evidence: `docs/research/2026-08-competitor-analysis.md` §Feature decisions.

| # | Feature | Original Priority | Reason | Evidence |
|---|---------|------------------|--------|----------|
| 7.6a | Prompt Auto-Optimization | P3 | Specialized frameworks have standardized this; maintaining a niche tool against specialized competition is negative ROI. **Boundary with delivered 6.19** (2026-09-15): what was dropped is a standalone optimizer product; 6.19 ships the eval-driven loop on Hecate's own 7.3a/7.3b assets with human approval and no gepa/DSPy dependency (re-open trigger in its design D1) | IBM watsonx AgentOps (Jul 2026, GEPA optimization, chat-based eval→optimize→deploy), DSPy (Stanford, mature) |
| 7.6b | Prompt Comparison | P3 | Evaluation platforms cover prompt comparison natively | LangSmith (100k+ MAU), Salesforce A/B Testing API (pilot, TDX 2026), Braintrust |
| 9.2a | Content Moderation | P3 | Model built-in safety layers + purpose-built free moderation APIs outperform platform-level moderation | GPT-5/Claude Opus 5 built-in safety, OpenAI Moderation API (free) |
| 6.17 | DSL Conversion Framework | P3 | MCP/A2A protocol standardization reduces DSL conversion value; industry converging on standard agent definitions | Salesforce Agent Script (open source, TDX 2026), MCP 97M downloads/month |
| 9.8 | Full-Chain Network Security | P3 | TLS/WAF/API Gateway/NetworkPolicy are infrastructure-layer concerns; belong in deployment guides, not platform features | All 18 surveyed platforms delegate to infrastructure (K8s NetworkPolicy, Istio, cloud WAF) |
