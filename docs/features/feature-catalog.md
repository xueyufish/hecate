# Hecate Feature Catalog

> **Date**: 2026-08-11
> **Status**: Updated — P1 complete (19/19); P2 complete (63/63); P3 in progress (80/125, 64%, 2026-08-14 re-scope); Environment Security P0 completed (9.12, 9.13, 9.14, 9.15); SIEM Pipeline completed (8.7 SS5: Webhook + Syslog + OCSF); Enterprise Identity complete (SSO/LDAP, SCIM, Budget Management, Vault Integration); Data Backup & Recovery complete (13.5, 2026-07-31); Sandbox Container Pool complete (9.4d, 2026-07-29); Distributed Session State Store complete (13.4a 5/5 + 13.4a-6 deprecation, 2026-08-07); Version Upgrade complete (13.6, 2026-08-09); Outbound DLP Engine complete (9.10, 2026-08-10); 13.4b/9.16a/13.4c/13.19 added as deferred scope tracking for 13.4 minimal P0 unlock (2026-08-11); Model Hub complete (Model Catalog, Model Lifecycle Manager, Intelligent Router, Model Cost Management, Multi-Modal Classification, Fine-Tuning Pipeline, Model Monitoring); Ops Center features added (9 new features: Unified Ops Center Dashboard, Agent Health Monitoring, Conversation Analytics, Testing Center, Budget Management, Environment Management, Compliance & Audit Center, CI/CD Evaluation Gating, Agent Catalog Governance & Quality Gateway; 6 enhancements: Custom Dashboard Builder, Incident Management Console, Model Management Console, API Management Portal, Backup/Restore Console, Conversation Topic Clustering); Model Hub features added (3 new features: Model Catalog, Model Lifecycle Manager, Model Governance; 5 enhancements: Managed Model Deployment, Model Cost Management, Multi-Modal Classification, Fine-Tuning Pipeline, Model Monitoring); Tool Platform features added (2 new features: Plugin Security & Signing, Tool Execution Analytics Dashboard; 4 enhancements: Composable Tool Policy Pipeline, Plugin Type Taxonomy + Developer SDK, Per-Tool Auth Scope, Session Events + Tool Matchers); Knowledge & Memory features added (2 new features: Temporal Memory & Reasoning, Lazy GraphRAG; 4 enhancements: Sleep-time Memory Consolidation, DRIFT Search, Schema-Aware Traversal, Work Context Graph); Enterprise Foundation features added (2 new features: Outbound DLP Engine, Enterprise Vault Integration; 4 enhancements: Data Lineage Pipeline, Multi-Region Data Sovereignty, Zero Data Retention Policy, Confidential Computing Mode); Security Shield features added (2 new features: Agent Runtime Protection, Automated Continuous Red Teaming; 6 enhancements: Injection Type Detection, System Prompt Leakage Protection, Security Event SIEM Pipeline, Multi-Agent Trust Verification, Adversarial Test Generation); Ecosystem features added (2 new features: Agentic Resource Discovery, Partner Monetization Infrastructure; 4 enhancements: Semantic Marketplace Discovery, Community Agent Gallery, Cross-Surface Experience Layer, Governed Agent Catalog); Observability & Evaluation features added (2 new features: CI/CD Evaluation Gating, Agent Catalog Governance & Quality Gateway; 8 enhancements: OTel GenAI Semantic Conventions Alignment, Production Online Scoring, Data-to-Decision Full-Chain Traceability, Conversation Topic Clustering, Multi-Agent Distributed Tracing, Evaluation Metrics Three-Dimension Structuring, Reasoning Efficiency Evaluator, Adversarial Test Generation); Agent Runtime features complete (3 new: Agent Environment 1.3.15, Agent State Separation 1.3.16, Agent Invocation Mode 1.3.17; Handoff 2.2 enhanced with context_mode strategies inherited/isolated/summarized, per-target tool descriptions, AIMessage+ToolMessage pairing); **2026-08-14 re-scope** (per docs/research/2026-08-competitor-analysis.md + 2026-08-deepseek-harness-analysis.md): dropped 5 (7.6a/7.6b/9.2a/6.17/9.8 — see Dropped Features appendix), deferred 17 to P5 (OCR/Table/Layout, KG suite incl 3.5.5+3.1.8, High-Throughput Retrieval, Model UI ×4, Canvas Embedding ×3, Decision Lineage), added 1.3.18 Dynamic Orchestration + 1.3.19 Event-Sourced State + 8.20 Run Replay + 6.27 Browser Automation (from P4) to P3, added 2.13 ACP + 8.21 Projection Registry + 13.20 Atomic File Locks + 6.27a Computer-use to P4, moved 11.11 Voice Agent Pipeline P5→P4, enhanced 5.9 with Skill Provider Registry (subsumes proposed 5.12 Agent Skills Standard), renamed 5.9 Environment Security → 5.14 (ID collision fix); completed-feature upgrade notes added on 1.3.4 (fail-closed approval), 9.4 (content-aware gating + monotonic denial), 1.3.5i E3 (waterfall middleware chain), 4.13 (surface-replacement compaction); OE9 self-built evaluator frozen → OTel/LangSmith export; 7.5 A/B Testing rescoped to Agent-Level (models → 6.8a ✅, prompts → eval-platform integration); **2026-08-14b re-verification corrections** (19 platforms + 6 new entrants + 5 protocols, full evidence in .omo/ulw-research/20260814-212730/SYNTHESIS.md): A2A current spec is v1.0.x (a previously-cited "v1.2" never existed — fixed in roadmap risk table); deer-flow DeltaChannel is UNRELEASED (2.1.0 milestone) — OMA v1.15.0 durable-approval checkpointing is the shipped reference; Dify Agent App is Open Beta, not GA; AgentScope middleware is 7 hooks in Python (on_check_permission, 2.0.6) / 5-stage in Java; "Versatile" identified as Huawei's platform renamed AgentArts (智果) 2026-02 — 34 references re-attributed, "Versatile ET/OT Engine" module not found (misattribution removed); MCP 2026-07-28 spec (stateless core, MRTR, header routing) added as 5.4b migration TODO; Dogwood (AWS temporal policy language, Cedar+MFOTL) added to 9.16 adapters; 5.9 Skill registry gains mandatory security-scanning + pin-by-hash requirement (ClawHub injection findings); 1.3.19 gains dsh-invariants runtime-checker requirement
> **Purpose**: Map the complete capability domains of the enterprise-grade multi-tenant Agent platform, providing a basis for MVP planning and architecture decisions
> **Priority**: P1 (Usable, months 1-3) → P2 (Enterprise-Ready, months 4-6) → P3 (Trustworthy, months 7-9) → P4 (Intelligent, months 10-12) → P5 (Ecosystem, months 13+)

---

## Capability Domain Overview

Based on research of 32 projects (AgentArts, openJiuwen, Bailian, Qianfan, Coze, Dify, Bisheng, AgentScope, OpenClaw, HermesAgent, DeepAgents, AutoGen, CrewAI, LangGraph, LlamaIndex, RAGFlow, Mem0, Letta, LangFuse, Ragas, NeMo Guardrails, Langflow, LiteLLM, Temporal, MCP, A2A, Docling, RelayAgent, Microsoft Copilot Studio, Salesforce Agentforce, Google Vertex AI Agent Builder/ADK, IBM watsonx Orchestrate), we have identified 18 capability domains and 352 feature points.

---

## Statistics

| Priority | Features | Goal | Timeline | Done |
|----------|----------|------|----------|------|
| **P1 Usable** | 19 | Create Agent + Plan-Execute + Configure Model/Tool/Knowledge/Skills + Chat Testing + Self-Hosted Deployment | Months 1-3 | 19/19 (100%) |
| **P2 Enterprise-Ready** | 65 | Canvas + Workflow + Multi-Agent + Memory + Multi-Tenant + RBAC + Basic Evaluation + Basic Security + Basic Observability + Context Engineering + Validation + Scheduled Tasks + Prompt Management + Model Routing + Multi-DB + Multi-Vector-DB + MCP Server + Authentication + Canvas UI Enhancement + Collaboration Patterns + Agent Communication + Routing Rules + Model Playground + Offline Deployment + Webhook Callbacks | Months 4-6 | 65/65 (100%) |
| **P3 Trustworthy** | 125 | Full Evaluation Suite + Enhanced Security + Full Observability + Ops Center + Model Hub + Enterprise Governance + Multi-Channel + Advanced Model Management + Plugin System + Tool Platform + MCP Gateway + MCP Server Registry + Advanced RAG (rescoped) + Multi-Agent Enhancement + Memory Enhancement + SSO + Quota + Platform SPI + i18n + MCP Streamable HTTP + Skill Versioning + NL2Agent + Trace Annotation + Per-Token-Type Auth + Two-Tier Identity + Human Input/Form Node + Trigger Node + **Dynamic Orchestration (1.3.18)** + **Event-Sourced Execution State (1.3.19)** + **Run Replay (8.20)** + **Browser Automation (6.27)** + Distributed Session State Store (Redis) + **Data Backup & Recovery** + **Version Upgrade** + **Unified Ops Center Dashboard** + **Agent Health Monitoring** + **Conversation Analytics** + **Tool Execution Analytics** + **CI/CD Evaluation Gating** + **Agent Catalog Governance** + **Outbound DLP Engine** + **Agent Runtime Protection** + **Automated Red Teaming** + **Budget Management** + **Enterprise Vault Integration** + **Model Catalog** + **Model Lifecycle Manager** + **Environment Management** + **API Management** + **MCP Connection Management** + **Plugin Packaging** + **Collaborative Conflict Handling** + **Unified Skill Registry** + **Agent-Workflow Mutual Embedding** + **A2A Protocol** + **Signed Agent Cards** + **Multi-Agent Trust Verification** + **Cost Tracking (G8)** + **Multi-Modal Classification (G6)** + **Managed Model Deployment (G5)** + **Fine-Tuning Pipeline (G7)** + **Model Monitoring (O10+G4)** + **Agent Environment** + **Agent State Separation** + **Agent Invocation Mode** + **Sandbox Environment Mount** + **Environment Backend: Docker** + **Context Offloading** + **Environment Network Egress Control (9.12)** + **Sandbox Enforcement Integration (9.13)** + **Structured Security Audit Pipeline (9.14)** + **Per-Execution Credential Scoping (9.15)** + **SaaS Deployment (13.1)** + **K8s Scaling Test Enhancements (13.4b)** | Months 7-14 | 80/125 (64%) |
| **P4 Intelligent** | 100 | Self-Learning + Hallucination Detection + Agentic RL + Prompt Self-Optimization + Ontology Actions + OAG + Advanced RAG + GraphRAG + Agentic RAG + Memory Integration + Temporal Memory + Lazy GraphRAG + Intelligent Router + Canvas UI + SCIM + Deterministic Hooks + Skill Auto-Detection + Skill Dependency + 5-Level Intent + Object Logs + Object History + Simulation + Computer-use (6.27a) + DataAgent + VibeCoding + Fine-Grained Permissions + Data Integration + gVisor Sandbox + Kata Containers + Decision Simulation + Multi-Stream Modes + Object CRUD Node + Side-by-side Chat+Canvas + Asynchronous Execution API + Peer Selection + Agent Team Templates + Distributed Team Orchestration + **ACP (2.13)** + **External Policy Engine Interface (9.16)** + **AI Auto-Approval (9.17)** + **Chaos Engineering for Multi-Replica (9.16a)** + **Session-Level microVM Isolation (13.4c)** + **Service Mesh Integration (13.19)** + **Projection Registry (8.21)** + **Atomic File Locks (13.20)** + **Voice Agent Pipeline (11.11)** | Months 15-18 | 4/100 (4%) |
| **P5 Ecosystem** | 60 | Asset Marketplace + Plugin Security & Signing + Partner Monetization + Agentic Resource Discovery + Industry Capabilities + Compliance + Vision + Desktop + End-User App + PyPI + AgentSpace SDK + EU AI Act + W3C Trace Context + Agent Benchmarks + Edge/Lite + AP2 + Knowledge Graph Viz + Ontology Modeling + Memory Versioning + AI Office + Industrial Data + Asset Operations + Memory Clustering + Self-Planning + Tool Auto-Creation + Firecracker + Cloud Doc Connector + Global Branching + Embedded Ontology + Platform-Level Governance + Zero Trust + **Compliance & Audit Center** + **Model Governance** + **Firecracker microVM Backend (6.40)** + **WASM Runtime Backend (6.41)** + **17 items deferred from P3/P4 (2026-08-14)** | Months 13+ | 0/60 |
| **Total** | **369** | | | **168/369 (46%)** |

---

## P1: Usable (Months 1-3)

> Users can create Agents, configure models and tools, upload knowledge bases, test via API chat, and deploy on-premises.

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 1.3.1 | ReAct Agent Loop ✅ | Agent Runtime | Standard Think→Act→Observe loop | LangGraph, DeepAgents |
| 1.3.1a | Plan-Execute Task Decomposition ✅ | Agent Runtime | Plan Agent auto-analyzes tasks, selects Skills, decomposes into sub-tasks; supports Guard→Plan→Sub-Agent three-layer architecture | RelayAgent (three-layer Agent), DeepAgents (task tool) |
| 1.3.2 | Tool Calling ✅ | Agent Runtime | Agent calls tools with parallel execution, retry, and timeout support | Claude Code (40+ tools), OpenClaw (TypeBox) |
| 1.3.3 | Streaming Output ✅ | Agent Runtime | Real-time streaming of Agent responses and tool call progress | LangGraph (7 stream modes) |
| 1.3.4 | Human Intervention ✅ | Agent Runtime | Execution pause, human approval, result correction, resume continue. **Planned enhancement (2026-08-14, Sprint 7)**: Fail-closed approval — no answerer or throwing answerer → deny (dsh `'unavailable'` semantics); only explicit allowed-once grants; `ask`/`never` policy state machine as durable config; `approval/asked` + `approval/decided` audit pair as EventStore events, turn-enclosed. Sequenced after 1.3.19 (audit pairs consume the enriched event log). **Seam notes (1.3.19 已交付)**: 增量加 `APPROVAL_ASKED`/`APPROVAL_DECIDED` EventType 枚举值（未知类型回读回落 `CUSTOM` 已保兼容）；审计对须 turn-enclosed（`TURN_START`/`TURN_END` 边界）；复用 1.3.19 的 B2 日志推导恢复路径（见 [ADR-030 下游消费者接缝登记表](../design/adr/030-event-sourced-execution-state.md)）。 | Bisheng (workflow pause), LangGraph (interrupt), dsh approval seam (source-verified), MAF durable approvals, AgentScope PermissionEngine |
| 1.3.5 | Error Recovery ✅ | Agent Runtime | Tool failure retry, model degradation, graceful degradation | OpenClaw (14 failure types) |
| 3.1.1 | Document Parsing ✅ | Knowledge Base/RAG | PDF/Word/PPT/Excel/HTML/Markdown parsing | Docling (20+ formats), RAGFlow DeepDoc |
| 3.2.1 | Vector Search ✅ | Knowledge Base/RAG | Embedding + ANN search | LlamaIndex, Qdrant |
| 3.2.6 | Chunking Strategy ✅ | Knowledge Base/RAG | Auto-chunking, character-based, separator-based, semantic chunking | Coze (4 strategies), LlamaIndex |
| 3.3.1 | Knowledge Base CRUD ✅ | Knowledge Base/RAG | Create, update, delete knowledge bases and documents | Coze, Dify |
| 5.1 | Built-in Tools ✅ | Tools & Plugins | Code execution, Web search, file operations, etc. | DeepAgents (7 file tools), CrewAI (30+ built-in) |
| 5.2 | Custom Tools ✅ | Tools & Plugins | User-defined tools (function/API Schema) | OpenClaw (TypeBox), Claude Code (buildTool) |
| 5.3 | MCP Client ✅ | Tools & Plugins | Connect to external MCP servers, discover and call tools | OpenClaw, OpenCode |
| 5.9 | Skill Loading & Management ✅ | Tools & Plugins | SKILL.md format knowledge/instruction packages, on-demand loading into context; full CRUD API + SKILL.md import + agent-skill association + SkillLoader service with XML injection into system prompts + auto_load + token budget. **P3 TODO (2026-08-14)**: Skill Provider Registry — provider registry with source origins (project/user/bundled/custom) + rank precedence (lower rank wins) + kebab-case name grammar + model/user invocation policy separation. Subsumes the proposed "5.12 Agent Skills Standard" (agentskills.io / ClawHub community skill loading) — implemented as a 5.9 enhancement, not a separate feature. **Security hardening requirement (2026-08-14 re-verification)**: SKILL.md has no versioning/trust layer ("npm circa 2012") and third-party skill stores carry real injection risk (Snyk audit: 36% of ClawHub skills contain prompt injection; ClawHavoc campaign) — registry MUST ship built-in skill scanning/prompt-injection vetting + pin-by-source-hash (format has no semantic versioning; `allowed-tools` remains experimental). | OpenCode, Claude Code, OpenClaw (ClawHub), RelayAgent, dsh (skill provider registry, source-verified), AgentScope (skill hubs 2.0.6), Hermes Skills Hub (security-scanned) |
| 6.1 | Multi-Model Access ✅ | Model Management | Support 100+ LLM providers | LiteLLM (OpenAI/Anthropic/Google/Baidu/Alibaba/ByteDance…) |
| 6.3 | Model Degradation ✅ | Model Management | Auto-switch to fallback model when primary is unavailable | OpenClaw (14 failure types), HermesAgent (fallback chain) |
| 8.4 | Conversation Logs ✅ | Observability & Operations | Complete conversation history and tool call records | Coze (debug panel), Dify |
| 11.1 | API Interface ✅ | Multi-Channel Access | REST API + WebSocket, OpenAI compatible format | Coze, Dify |
| 13.2 | Self-Hosted Deployment ✅ | Deployment & Operations | Docker Compose / K8s one-click deployment | Dify, Bisheng |

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
| 1.1.2 | Visual Workflow Canvas ✅ | Agent Development | Drag-and-drop DAG editor with node connections, conditional branching, parallelism. **P3 TODO**: integrate ConfigPanel into right-side panel; persist node layout to localStorage | Coze (DAG), Langflow (React Flow) |
| 1.1.3 | Workflow Node Type Library ✅ | Agent Development | LLM, Code, Condition, Tool, Knowledge Base Retrieval, Variable, Batch Processing nodes. **P3 TODO**: add fan-out/merge node creation and editing (currently visual-only, no palette/config) | Coze (8 types), Dify |
| 1.1.4 | Workflow Test Run ✅ | Agent Development | Step debugging, input/output preview, execution logs. **Planned enhancement (G7)**: Agent Debug Inspector — superstep-level state inspector showing Channel values, node inputs/outputs, and execution timeline at each BSP barrier. Channel-level state visualization complements node-level step debugging. | Dify, Coze, LangGraph Studio |
| 1.1.5 | Scenario-based Agent Packaging ✅ | Agent Development | Package configured Agents (persona + tools + knowledge + skills + channels) into reusable scenario solutions | AgentArts (scenario Agent) |

### Multi-Agent Orchestration

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 2.1 | Hierarchical Delegation ✅ | Multi-Agent Orchestration | Parent Agent spawns child Agents, context isolation, result aggregation | DeepAgents (task tool), OpenClaw (subagent) |
| 2.2 | Handoff ✅ | Multi-Agent Orchestration | Agent returns another Agent from tool function, control transfer. Wires `handoff_to_agent` tool injection into `AgentExecutionPort.agent_execute()` based on outgoing handoff/dynamic_handoff edges; `AgentWorker` translates tool call into `WorkerResult(command=Command(goto=target))` honored by PregelRuntime. Three context-passing strategies via `handoff.context_mode`: `inherited` (default, full history — matches OpenAI Swarm), `isolated` (fresh context — matches Claude Code subagent), `summarized` (LLM-generated structured summary — matches OpenAI `nest_handoff_history`). AIMessage + ToolMessage pairing preserves valid conversation history. Per-target tool descriptions sourced from `handoff.description` > `AgentModel.description` > node `name`. | OpenAI Agents SDK (handoff_description), Google ADK (agent.description), AutoGen Swarm, LangGraph (Command(goto=...)) |
| 2.3 | Pipeline ✅ | Multi-Agent Orchestration | Deterministic multi-step process, data flow between stages | CrewAI Sequential, AgentScope Pipeline |
| 2.4 | Broadcast ✅ | Multi-Agent Orchestration | Shared message space visible to all participants | AgentScope MsgHub |
| 2.7a | Collaboration Pattern Selection ✅ | Multi-Agent Orchestration | Select collaboration pattern on canvas: Sequential (linear chain), Parallel (fan-out/merge), Handoff (control transfer), Broadcast (shared context), Negotiation (proposer-responder loop), Debate (alternating arguments). Pattern selection auto-configures node layout and edge types. | Coze (Multi-Agent), AgentArts, CrewAI (process types), LangGraph (workflow agents) |
| 2.7b | Agent Communication Configuration ✅ | Multi-Agent Orchestration | Configure inter-agent communication: shared channel selection (readable/writable), message passing protocol, state mapping between agents. Configured per Agent node in canvas. | AgentArts (agent config), LangGraph (shared state), AutoGen (message passing) |
| 2.7c | Routing Rule Configuration ✅ | Multi-Agent Orchestration | Configure routing rules for multi-agent workflows: intent-based routing (user intent → agent selection), condition-based routing (data-driven branching), dynamic routing (LLM-driven next-speaker selection). Canvas UI for condition nodes and controller patterns. | AgentArts (multi-agent controller), Huawei Pangu (intent routing), AutoGen (SelectorGroupChat) |

### Memory System

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 4.1 | Working Memory (L1) ✅ | Memory System | In-context memory, updated each turn | Letta (MemoryBlock) |
| 4.2 | Session Memory (L2) ✅ | Memory System | Conversation history with auto-compression | Claude Code (5-level compression), OpenClaw (Context Engine) |
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
| 1.3.9 | Task Queuing ✅ | Agent Runtime | Sequential task processing within a session, new messages auto-queue to avoid concurrency conflicts. **Planned enhancement (G9)**: Background Task Offloading — long-running tool calls (>30s) offloaded to background execution, agent continues reasoning on other tasks, tool results wake agent upon completion via EventStore notification. Changes the synchronous Pregel superstep model to support async tool completion. | AgentScope (background task offloading), openJiuwen (event-driven multi-agent control) |

### Low-Code Enhancement

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 1.1.8 | Conversational vs Task Workflows ✅ | Agent Development | Two workflow modes: conversational (multi-turn) and task (single execution, API-callable). **Planned enhancement (G6)**: Chatflow ↔ Workflow mode conversion — lossless conversion between conversational and task modes, preserving node graph while stripping/adding conversation context parameters (session binding, conversation history reference). | AgentArts (dual-mode workflow), Coze (Workflow ↔ Chatflow conversion) |
| 1.1.9 | Workflow Version Management ✅ | Agent Development | Versioned workflow releases, diff comparison, rollback, commit required before publishing | AgentArts (workflow versioning) |
| 1.1.10 | App Import/Export ✅ | Agent Development | Full Agent app import/export for backup, migration, cross-environment replication | AgentArts (app import/export) |
| 6.7 | Model Playground ✅ | Model Management | Built-in model test UI for connectivity and response quality debugging, parameter tuning. Debug page at settings/models/debug with model selector, parameter tuning (temperature, max_tokens), streaming display, latency/usage visualization, test history, and error suggestions. | AgentArts (model debugging) |

### Canvas UI Enhancement

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 1.1.14 | Agent Node Config Enhancement ✅ | Agent Development | Enhance Agent node config panel: role description, invocation mode (direct/tool), readable/writable channel selection, model override. Currently only has agent_ref field. | AgentArts (agent config), Coze (bot config) |
| 1.1.15 | Template Customization ✅ | Agent Development | After loading orchestration template, edit Agent roles, add/remove Agent nodes, adjust connections, save as new workflow. Currently template loading is one-shot. | Coze (template edit), CrewAI (crew studio) |
| 1.1.16 | Typed Edge Visualization ✅ | Agent Development | Edge types with visual differentiation: default (solid), handoff (dashed purple), conditional (dotted labeled), fan-out (multi-arrow). Edge type selector on connect. | LangGraph (edge types), CrewForm (edge labels) |
| 1.1.17 | Fan-Out/Merge Node Editing ✅ | Agent Development | Make fan-out and merge nodes draggable from palette and configurable (branch targets, merge strategy). Currently visual-only, no palette/config support. | Coze (parallel nodes), Dify (parallel structure) |

### Scheduling & Automation

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 13.9 | Scheduled Tasks ✅ | Deployment & Operations | Cron-triggered Agent/Workflow execution, supports periodic/interval/one-shot, result push to channels | AgentArts (scheduled tasks) |

### Context Engineering

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 4.7 | Context Assembler ✅ | Memory System | Assembles optimized context for LLM invocations from messages, tools, and knowledge | AgentArts (context engineering), OpenClaw (Context Engine) |
| 4.8 | Evidence Tracker ✅ | Memory System | Captures tool execution results with provenance tracking, importance scoring, and re-reference boosting | AgentArts (evidence tracking) |
| 4.9 | Task Phase Detection ✅ | Memory System | Detects task phases (EXPLORE/CONVERGE/EXECUTE/VERIFY) for dynamic tool and context filtering | AgentArts AgentBase (formerly Versatile) (phase detection) |
| 4.10 | Token Budget Governance ✅ | Memory System | Per-session token budget tracking with three-level degradation (DROP/COMPRESS/EMERGENCY) and budget snapshot persistence | Claude Code (5-level compression) |
| 4.11 | Provider-Shaped Context ✅ | Memory System | Provider-specific context shaping strategies (OpenAI, Anthropic, Default) with automatic model prefix detection | AgentArts (provider adaptation) |
| 4.12 | Message Prioritization ✅ | Memory System | Prioritizes messages by importance for context window optimization | OpenClaw (Context Engine) |

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
| 13.3 | Offline Deployment ✅ | Deployment & Operations | Air-gapped / offline deployment for regulated environments (government, defense, finance). Full bundle without external network dependencies: pre-built Docker images, embedded model weights, local LLM runtime (Ollama/vLLM), offline package registry mirror, internal-only Helm chart values, no telemetry/phone-home. Distinct from 13.2 (Self-Hosted) which assumes internet access for image pulls and model API calls. | Palantir Apollo (air-gapped SaaS via cryptographically signed bundles), Bedrock AgentCore (VPC-only MicroVM), openJiuwen (offline installer), 华为 AgentArts (private deployment) |
| 14.2 | Webhook Callbacks ✅ | Deployment & Operations | Outbound webhook callbacks for platform events (agent completed, workflow finished, tool error, threshold alert). Configurable per-workspace webhook endpoints with HMAC-SHA256 signatures, retry with exponential backoff, dead-letter queue for failed deliveries. Complements 8.7 SIEM Pipeline (inbound security events) with outbound automation triggers. Foundation for 11.x channel push notifications and 13.9 Scheduled Tasks result delivery. | Dify (webhook node), Slack/Stripe webhook patterns (HMAC signatures), openJiuwen (event subscription) |
| 3.1.7 | Multi-Vector-DB Support ✅ | Knowledge Base/RAG | Pluggable vector database backends: Qdrant, Milvus, Weaviate, Chroma; unified vector operations interface with per-collection backend selection | openJiuwen (4 vector DBs), LlamaIndex (vector store abstraction) |
| 5.9a | MCP Server Mode ✅ | Tools & Plugins | Expose Hecate capabilities (Agent execution, Knowledge retrieval, Tool invocation) as MCP tools via MCP server; enables external platforms to consume Hecate as a tool provider | OpenClaw (bidirectional MCP), openJiuwen (fastmcp) |

### Multi-Tenant & Enterprise

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 10.1 | Organization Management ✅ | Multi-Tenant & Enterprise | Multi-org isolation, multiple workspaces under org with owner transfer support | Coze (Spaces), Bisheng |
| 10.2 | RBAC ✅ | Multi-Tenant & Enterprise | Workspace-level role-based access control (admin/editor/viewer) with FastAPI dependency guards | Bisheng (deep RBAC + user groups) |
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
| 9.1a | Guardrails ✅ | Security & Compliance | AI-powered guardrails to detect and block prompt injection attacks, filter sensitive info, mutually exclusive with keyword moderation. Reimplemented as engine-level PreLLMHook/PostLLMHook with SANITIZE action; NeMo stub removed. **Planned enhancement (SS3)**: Injection Type Detection — detect downstream system injection patterns in LLM outputs: Code injection (Python `exec`/`eval`), SQL injection (`DROP TABLE`/`UNION SELECT`), Template injection (Jinja `{{ }}`), XSS (`<script>`). When agent output flows to code interpreter, database query, template renderer, or HTML page, these patterns are detected and blocked/escaped before reaching downstream systems. YARA rule-based pattern matching with custom rule support. | NeMo Guardrails v0.22 (injection detection: code/sqli/template/xss with YARA rules), AgentArts (guardrails) |
| 9.2 | Output Security ✅ | Security & Compliance | Output toxicity detection, PII deanonymization via OutputSecurityHook (PostLLMHook); StreamDeanonymizer for streaming-safe PII restoration. **Planned enhancement (SS4)**: System Prompt Leakage Protection (OWASP LLM07:2025) — detect and block system prompt content exfiltration. Compares LLM output against system prompt content fingerprints (hash-based + semantic similarity). Blocks responses that reproduce confidential instructions, embedded secrets, or security rules from the system prompt. Prevents prompt injection attacks that attempt to extract the system prompt via "repeat your instructions" or "what are your rules" queries. | NeMo Guardrails, LLM Guard, OWASP LLM07:2025 (System Prompt Leakage) |

### Observability Foundation

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 8.1 | Full-Chain Tracing ✅ | Observability & Operations | Trace → Span → Generation hierarchical tracing with OTel context propagation, async SQLAlchemy persistence, and REST API. **Planned enhancement (OE2)**: OTel GenAI Semantic Conventions Alignment — standardize Span attributes to OpenTelemetry GenAI semantic conventions (`gen_ai.operation.name`, `gen_ai.request.model`, `gen_ai.agent.id`, `gen_ai.usage.total_tokens`, `gen_ai.tool.name`, etc.). Ensures seamless interoperability with OTel-compatible backends (Jaeger, Grafana, Datadog, Langfuse). | LangFuse (Session > Trace > Observation), OpenTelemetry GenAI Semantic Conventions (2025-2026) |
| 8.7 | Audit Logs ✅ (SIEM Pipeline ✅) | Observability & Operations | User operation audit trail, compliance requirements. **SIEM Pipeline (SS5) implemented**: structured security event export to enterprise SIEM systems via Webhook (Splunk HEC/Datadog/Elastic), Syslog (RFC 5424 TCP/UDP/TLS), and OCSF v1.5 formatter. Three-source unified event model (AuditLog + ToolDecision + SecurityFinding). Configurable event filtering and severity mapping. **Naming refactor**: SecurityAudit → ToolDecision, PolicyEngine → FindingEngine (aligned with OCSF Activity/Authorization/Finding classes). | Cisco AI Defense (SIEM integration), Enterprise security standard (CEF/LEEF/OCSF) |

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

> Full evaluation suite, enhanced security, full observability, **Ops Center**, **Model Hub**, **Enterprise Governance**, multi-channel access, advanced model management, plugin system, **Tool Platform**, advanced RAG, memory enhancement, SSO, canary release, self-evolution, meta-agent operations, data backup, version upgrades, NL2Agent, Trace Annotation, Per-Token-Type Auth, Two-Tier Identity, Human Input/Form Node, Trigger Node, Distributed Session State Store, **Event-Sourced Execution State (1.3.19)**, **Dynamic Orchestration (1.3.18)**, **Run Replay (8.20)**, **Browser Automation (6.27)**. *(2026-08-14 re-scope: DSL Conversion + Decision Lineage dropped/deferred; OCR/Table/Layout + Knowledge Graph + Model UI + Canvas Embedding deferred to P5 — see P5 deferred section.)*

### Enterprise

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 10.3 | SSO/LDAP ✅ | Multi-Tenant & Enterprise | Enterprise identity federation via OIDC, SAML, and LDAP. JIT user provisioning on first SSO login. | Bisheng (SSO + LDAP) |
| 10.4 | Quota Management ✅ | Multi-Tenant & Enterprise | Per-tenant API call, storage, compute resource limits | Coze (resource points) |

### Evaluation & Testing

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 7.2a | 40+ Built-in Evaluators ✅ | Evaluation & Testing | Pre-built evaluator library: correctness, hallucination (groundedness validation), toxicity, instruction following, citation relevance, tool selection, format check, programmatic evaluators, tool trajectory scoring, multi-turn task success, multi-turn trajectory quality, safety/harmlessness, LLM-as-judge semantic match. **Planned enhancement (OE8)**: Evaluation Metrics Three-Dimension Structuring — organize all evaluators into Effectiveness (task completion, answer accuracy, RAG quality), Efficiency (reasoning steps, tool call frequency, per-task cost), and Safety (content violation rate, unauthorized operations, jailbreak pass rate) dimensions with structured dashboard display. **Planned enhancement (OE9) — frozen (2026-08-14 research)**: Reasoning Efficiency Evaluator — self-built evaluator expansion is on hold per competitor analysis ("Hecate's role = trace data source via EventStore + OTel export, not evaluation executor"); redirect the superstep/tool-span metrics to OTel span attributes consumed by LangSmith/Langfuse (see 8.10 + Evaluation integration direction). Includes LLM-as-Judge bias mitigation strategies (multi-judge averaging, randomized evaluation order, anchored scoring criteria). | AgentArts (40+ evaluators), Google ADK (10 evaluation metrics) |
| 7.2b | AI-Synthesized Evaluation Dataset | Evaluation & Testing | LLM auto-synthesizes evaluation datasets from seed data, supports adversarial and security compliance samples | AgentArts (AI-synthesized dataset) |
| 7.2c | Online/Offline Evaluation Tasks | Evaluation & Testing | Online real-time sampling evaluation + offline batch evaluation, supports Trace/Model/Root/Tool granularity. **Planned enhancement (OE3)**: Production Online Scoring — real-time sampling of production traffic with LLM-as-Judge auto-scoring, capturing live quality regressions. Continuous quality monitoring on production traffic without waiting for offline batch runs. | AgentArts (evaluation tasks), Braintrust (online scoring), Langfuse (production evaluation) |
| 7.2d | Trace Backflow Dataset | Evaluation & Testing | Production Trace data directly flows back to evaluation dataset, supports field mapping | AgentArts (Trace backflow) |
| 7.2e | Evaluation Report Dashboard | Evaluation & Testing | Automated evaluation reports: task success rate, score distribution, evaluator dimension charts | AgentArts (evaluation reports) |
| 7.3 | Workflow Evaluation | Evaluation & Testing | End-to-end workflow testing, regression testing | Bisheng (version-level testing) |
| 7.4 | Human Annotation | Evaluation & Testing | Human review of Agent output, labeling and scoring | Bisheng (annotation tasks + annotator assignment) |
| 7.4a | Human Score Calibration | Evaluation & Testing | Human override of evaluator scores with reason annotation, update aggregate statistics | AgentArts (human calibration) |
| 7.6 | Regression Test Set ✅ | Evaluation & Testing | Maintain test datasets, CI/CD integration | Promptfoo (CI/CD integration) |

> **Dropped (2026-08-14)**: 7.6a Prompt Auto-Optimization, 7.6b Prompt Comparison — DSPy/IBM AgentOps (GEPA) standardized optimization; LangSmith/Salesforce A/B API standardized comparison. See "Dropped Features" appendix.

### Security

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 9.4 | Execution Security ✅ | Security & Compliance | Tool call approval, code sandbox isolation, access control, four-level risk authorization (LOW/MEDIUM/HIGH/CRITICAL, supports once/session/project/global scope). **Planned enhancement (2026-08-14, Sprint 7)**: Content-aware gating — bash pipeline static analysis (AgentScope `_bash_parser` pattern) for permission decisions beyond risk_level; monotonic denial invariant — guards can only deny, no listener ordering can resurrect a denied call (dsh ToolLayer.guardReason). Sequenced after 1.3.19. **Seam notes (1.3.19 已交付)**: 注册单调拒绝 invariant 进 LogInvariants 运行时校验层；复用 `CHANNEL_WRITE_REJECTED` 的"被拒=审计事件"范式（fold 跳过、审计保留）。 | OpenClaw (exec approval + sandbox), RelayAgent (four-level risk authorization), AgentScope PermissionEngine (bash static analysis), dsh monotonic guards (source-verified), Claude Code auto mode |
| 9.4a | Granular Operation Approval ✅ | Security & Compliance | Independent security approval config for 40+ operations (bash, write_file, mcp_exec_command, etc.), individually toggleable | AgentArts (40+ operation approval) |
| 9.4b | Trusted Workspace ✅ | Security & Compliance | File operations within workspace directory auto-allowed, operations outside require explicit approval | AgentArts (trusted workspace) |
| 9.5 | Data Security ✅ | Security & Compliance | PII masking in tool results via ToolResultSecurityHook; configurable storage modes (mask_only/mask_and_encrypt); Fernet encryption for PII mapping; PIIMappingModel ORM + audit; per-agent data_security guardrail config | LLM Guard (PII detection) |
| 9.5a | Sensitive Data Auto-Masking | Security & Compliance | Auto-detect and mask credentials, keys, ID numbers, etc. in conversation content and logs | AgentArts (auto-masking) |

> **Dropped (2026-08-14)**: 9.2a Content Moderation — model built-in safety layers + OpenAI Moderation API (free, purpose-built) cover most content moderation. 9.8 Full-Chain Network Security — TLS/WAF/API Gateway belong to the infrastructure layer (K8s/Istio/cloud WAF), not platform features. See "Dropped Features" appendix.
| 9.12 | Environment Network Egress Control ✅ | Security & Compliance | Per-environment application-level network egress control for DockerEnvironment. Network namespace isolation, allowedDomains/deniedDomains configuration, egress traffic proxy with request logging. Distinct from 9.7 (generic agent network sandboxing) and 9.8 (infrastructure-level TLS/WAF, dropped 2026-08-14) — this feature operates at the **environment level**, controlling what network destinations an agent's sandbox can reach. P0 sub-item of 5.14 Environment Security. **Depends on**: 1.3.15a ✅ DockerEnvironment. | Claude Code (sandbox.network.allowedDomains/deniedDomains), Codex CLI (destination rules + network_proxy), Dify (Squid proxy + K8s Egress), Google Vertex AI (VPC-SC), Bedrock (VPC-only MicroVM) |
| 9.13 | Sandbox Enforcement Integration ✅ | Security & Compliance | Guarantees that EXECUTE_SANDBOX decisions from ToolAccessPolicy actually execute inside DockerEnvironment/gVisor. ToolWorker routes tool execution based on AccessDecision: EXECUTE runs directly, EXECUTE_SANDBOX routes through DockerEnvironment exec_shell with sandbox container. Sandbox escape detection (container exit verification) + audit alert on anomaly. Closes the gap where sandbox_enabled is currently just a flag with no enforcement. P0 sub-item of 5.14 Environment Security. **Depends on**: 9.4 ✅ Execution Security, 9.4c ✅ Docker Sandbox Executor, 1.3.15a ✅ DockerEnvironment. | Claude Code (permission+sandbox dual-layer enforcement), Codex CLI (sandbox_mode × approval_policy matrix), Bedrock (Gateway boundary enforcement outside agent code) |
| 9.14 | Structured Security Audit Pipeline ✅ | Security & Compliance | Tool policy decision audit (renamed from SecurityAudit → ToolDecision). `ToolDecisionModel` captures every tool policy evaluation (tool_name, arguments_hash, decision, reason, policy_version, per-layer breakdown). Async batch writer. REST API `GET /api/security/decisions`. Three emission points: visibility, execution, access policy. Foundation for 8.7 SIEM Pipeline. **Renamed**: PolicyEngine → FindingEngine, PolicyViolation → SecurityFinding (persisted to `security_findings` table). | Bedrock (OCSF 99001 audit events with request_id/identity/delegation_chain/per-layer-decisions/latency → CloudWatch), Codex CLI (OpenTelemetry log export), IBM watsonx (Governance Graph), DeerFlow (SandboxAuditMiddleware), AWS Cedar multi-agent (OCSF 99001 per-decision audit) |
| 9.15 | Per-Execution Credential Scoping ✅ | Security & Compliance | Runtime credential isolation: tools receive only their scoped credentials at execution time, not all environment variables. CredentialScope configuration (per-tool credential mapping). Global env vars containing secrets (API keys, database passwords) are stripped from the agent process environment; credentials are injected per-tool-call via a secure channel. Distinct from 5.8 TP6 (per-tool auth connector scope at the connector level) — this feature operates at the **runtime execution layer**, preventing any tool from reading another tool's secrets. P0 sub-item of 5.14 Environment Security. **Depends on**: 10.8 ✅ Enterprise Vault Integration. | Codex CLI (two-phase runtime: setup phase has secrets → agent phase secrets removed), HermesClaw/OpenShell (credential stripping from agent + backend credential injection by sandbox), Bedrock (per-tool IAM role + Secrets Manager), Palantir (agent service user + OSDK scope) |

### Observability & Operations

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 8.2 | Real-Time Monitoring ✅ | Observability & Operations | Agent runtime status, error rate, latency | LangFuse |
| 8.3 | Cost Dashboard ✅ | Observability & Operations | Token and cost statistics by user/Agent/session | LiteLLM, LangFuse |
| 8.5 | Prompt Version Management ✅ | Observability & Operations | Prompt versioning, tag-based deployment (production/staging) — subsumed by 8.5a | LangFuse (Prompt Management) |
| 8.5b | Prompt Analytics & Diff ✅ | Observability & Operations | Version diff/comparison, change summaries (commit messages), per-version performance analytics linked to traces, protected labels (RBAC) | LangSmith (prompt diff), Vellum (release reviews) |
| 8.6 | Alerting ✅ | Observability & Operations | Error rate threshold, cost over budget, latency anomalies. **Planned enhancement (O9)**: Incident & Alert Management Console — centralized alert management with acknowledgement workflow, alert history, notification channel routing, silence rules, and escalation policies. Integrates with NotifierABC (8.6-abc) for multi-channel dispatch (PagerDuty, Slack, Email, Webhook). | Enterprise standard |

### Model Management

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 6.4 | Cost Tracking ✅ | Model Management | Token usage and cost statistics. Model Cost Management (G8) complete: per-model and per-workspace cost budgets with z-score anomaly detection, configurable enforcement (alert/block via PreLLMHook), spend forecasting, chargeback reports. Integrates with Budget Management (10.7). | LiteLLM (virtual keys + budget), LangFuse (cost tracking), Portkey (cost tracking) |
| 6.8 | Multi-Auth Support | Model Management | Model providers support 7 auth methods: Api-key, AK/SK, App-code, custom Header, IAM, HMAC, no-auth; compatible with Huawei Cloud, Alibaba Cloud, Baidu, etc. **Planned enhancement (EF5)**: Zero Data Retention Policy — provider registration declares retention policy (zero/limited/full); platform enforces zero-retention routing for sensitive workflows. Audit trail records which provider received what data classification level. Prevents external LLM providers from retaining or training on customer data. | AgentArts (7 auth methods), Salesforce Trust Layer (zero retention agreements) |
| 6.11 | Model Classification Management ✅ | Model Management | Model classification by purpose (Chat, Embedding, Completion, Rerank), category filtering, category-level quota control. Multi-Modal Model Classification (G6) complete: structured model_metadata JSON with modalities (input/output arrays), capabilities (reasoning/tool_call/vision flags), and limits (context/output). Router filters by modality. Catalog displays capability badges. | AgentArts (model classification), Vertex AI Model Garden |

> **Deferred to P5 (2026-08-14)**: 6.9 Provider Info Enhancement, 6.10 Key Security Enhancement, 6.12 Provider Auth State Management, 6.13 Model Management UI Redesign — UI redesign without users is speculative; defer until 13.1 SaaS Deployment launches and real user feedback exists (Dify spent $30M and years on UI).

### Tools & Plugins

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 5.4a | MCP Gateway | Tools & Plugins | Auto-convert REST/SSE/streaming APIs to MCP standard tools, unified gateway provides auth, routing, logging | AgentArts (MCP gateway) |
| 5.4b | MCP Streamable HTTP Transport | Tools & Plugins | Upgrade MCP implementation to 2025-03-26 spec: single `/mcp` endpoint (POST/GET), SSE upgrade for long-running tasks, stateless operation, standard load balancer compatible (no sticky sessions). Replaces dual-endpoint HTTP+SSE architecture. **Migration TODO (2026-08-14 re-verification)**: MCP spec 2026-07-28 (largest revision ever, released 2026-07-28) supersedes this — stateless core (initialize/Mcp-Session-Id removed, `_meta` self-describing requests), mandatory Mcp-Method/Mcp-Name header routing, Multi Round-Trip Requests (MRTR) replacing held-open elicitation/sampling streams (`resultType: input_required` + retry), cacheable list results (ttlMs/cacheScope), RFC 9207 iss validation, DCR→CIMD, Tasks moved to extension, Roots/Sampling/Logging + HTTP+SSE deprecated with 12-month offramp. Server-side implementation + client migration = M effort; official registry (registry.modelcontextprotocol.io) live with 1,000+ servers; governance at Linux Foundation Agentic AI Foundation. | MCP 2026-07-28 spec (supersedes 2025-03-26), Anthropic, ~500M SDK downloads/mo |
| 5.4c ✅ | MCP Server Registry & Connection Management | Tools & Plugins | Unified MCP Server registration, discovery, and connection management. **Server Registry**: MCP servers register with capabilities (tools/resources/prompts), clients discover servers by capability query, tool list caching (TTL-based). **Connection Management**: per-server connection pooling (HTTP: min/max sessions, stdio: single connection), automatic reconnection with exponential backoff (1s→2s→4s→...→60s, max 5 retries), per-request timeout control, connection lifecycle tracking (connecting/connected/disconnected/reconnecting/failed), health checks (periodic ping, consecutive failure marking), circuit breaker pattern (5 failures → open, 30s → half-open probe). **Multi-tenant isolation**: platform-level MCP servers share connection pool, workspace-level servers have isolated pools. REST API for connection status (`GET /api/mcp/connections`) and manual reconnect (`POST /api/mcp/connections/{name}/reconnect`). Merges former 5.4c (Server Registry) and 5.4d (Connection Management) into a single feature. | OpenClaw (MCP discovery + connection pooling), Claude Code (MCP connection management), Amazon Bedrock AgentCore Gateway (session management + auto-reconnect), mcpool (connection pool library) |
| 5.5 ✅ | Plugin System | Tools & Plugins | **Plugin runtime engine**: `plugin.yaml` manifest loading, local directory discovery (`plugins/` scan), basic `api_version` / `min_platform_version` compatibility check, extended `PluginLifecycle` (`on_enable` / `on_disable` / `on_config_change`), `PluginModel` DB table with per-workspace enablement, `config_schema` (JSON Schema) → DB storage → runtime injection, permission declaration + enforcement, REST API (list / enable / disable / configure), frontend plugin management page (list + config form auto-generation from `config_schema`), entry loading via `python:module:Class` (in-process importlib) and `mcp://endpoint` (via existing MCP Client 5.3), MCP endpoint connection management UI. Architecture: in-process + MCP hybrid (no custom daemon — out-of-process isolation deferred to platform infrastructure / WASM in P5). Security model: permission declaration + enforcement (Dify model), signing deferred to 5.13. **Does NOT include**: 6 plugin type ABCs (TP5), Python SDK module (TP5), packaging/distribution (5.5b). | Dify (plugin daemon + YAML manifest), OpenClaw (in-process + pluginApi compat), AgentArts (UI-driven config), Salesforce (code-first + UI config dual path) |
| 5.5 (TP5) ✅ | Plugin Type Taxonomy + Developer SDK | Tools & Plugins | **8 plugin types** classified by capability: **Tool Plugin** (callable function, new ToolPluginABC), **Extension Plugin** (hook/middleware injection, new ExtensionPluginABC wrapping existing Guardrail Hooks — Google ADK BasePlugin pattern), **Trigger Plugin** (event-driven invocation: webhook/schedule/MQ, new TriggerPluginABC), **Model Plugin** (custom LLM provider, new ModelPluginABC based on existing InferenceBackendABC), **Channel Plugin** (existing ChannelABC + plugin.yaml support), **Evaluator Plugin** (existing EvaluatorABC + plugin.yaml support), **Auth Provider Plugin** (existing AuthProviderABC + plugin.yaml support), **Secret Provider Plugin** (existing SecretProviderABC + plugin.yaml support). Built-in providers continue code registration; third-party providers load via plugin.yaml. `hecate.plugin` Python SDK module for type-safe plugin development. `hecate plugin init` CLI template generator. Hot-reload during development (file watcher → re-register). Full `pluginApi` install-time compatibility validation (API surface + SDK version checks). API-type plugin online creation UI (AgentArts-style form-driven). **Deferred**: Datasource Plugin (overlaps with knowledge base system), AgentStrategy Plugin (touches Worker core architecture, deferred to P4). **Depends on**: 5.5 runtime engine. | Dify (6 plugin types + SDK), Google ADK (BasePlugin callback pattern), OpenClaw (30+ capability types + pluginApi compat), HermesAgent (12+ plugin types), AgentArts (UI-driven plugin creation) |
| 5.6 ✅ | Tool Permission Control | Tools & Plugins | Tool-level access control with platform-level `available_when` gating — conditional expressions evaluated at Worker level; LLM cannot see or invoke tools whose conditions are unmet; per-tool approval workflow and scope-based visibility. **Planned enhancement (TP3)**: Composable Tool Policy Pipeline — multi-layer policy chain (profile → allow/deny → risk level → sandbox → channel → plugin availability) where each layer is a composable filter that can approve, deny, or transform tool calls. Policy DSL for declaring ordered tool access rules. Replaces single-condition gating with pipeline semantics. | Salesforce Agentforce (`available when`), OpenClaw (6-layer filtering pipeline) |
| 5.7 ✅ | Tool Caching | Tools & Plugins | Tool result caching to avoid duplicate calls | CrewAI (custom cache functions) |
| 5.8 | Enterprise System Integration Framework | Tools & Plugins | Pre-built ERP/CRM/OA connectors, standardized integration workflow. **Planned enhancement (TP6)**: Per-Tool Auth Scope — each tool connector maintains its own credential vault with isolated auth scope. Tool A cannot access Tool B's credentials. Supports OAuth 2.0 token management, API key rotation, and per-tool identity for enterprise audit. Enables tools like Salesforce connector, SAP connector, and ServiceNow connector to each have independent auth lifecycle. | AgentArts (AgentIdentity, per-tool URN + API Key), MCP + OpenAPI |
| 6.27 | Browser Automation Tool (moved from P4, 2026-08-14) | Tools & Plugins | Playwright-based browser tool: navigate, click, type, screenshot, extract content, fill forms. Registered as builtin in ToolRegistry; headless/headful configurable; sandboxed via DockerEnvironment. Computer-use portion split to 6.27a (stays P4). | Manus Browser Operator, Claude Code computer use, OpenClaw |
| 5.14 | Environment Security ✅ | Tools & Plugins | **Umbrella feature for agent execution environment security** *(renamed from 5.9 on 2026-08-14 — resolves ID collision with 5.9 Skill Loading & Management)*. Closes the gap between Hecate's existing tool-level security (5.6/9.4) and enterprise platform capabilities (Bedrock/Salesforce/Palantir/Huawei/Alibaba). Phase 0 (P0): network egress control (9.12), sandbox enforcement integration (9.13), structured audit pipeline (9.14), per-execution credential scoping (9.15). Phase 1 (P4): external policy engine interface (9.16), AI auto-approval (9.17). Per-environment tool permission policies integrated with ToolPolicyPipeline remain as a follow-on sub-item. **Depends on**: 5.6 ✅ Tool Permission Control, 1.3.15 ✅ Agent Environment, 9.4 ✅ Execution Security. | Palantir AIP (Ontology unified security: marking+purpose+role, three permission sources), Bedrock AgentCore (Cedar Policy at Gateway boundary, Lambda MicroVM), Claude Code (permission+sandbox dual-layer, 6 permission modes), Codex CLI (OS-native sandbox, auto_review, two-phase runtime), DeerFlow (GuardrailMiddleware + OAP Passport), Salesforce (Einstein Trust Layer, zero retention), AgentScope (multi-type sandbox, middleware system), 华为 AgentArts (microVM isolation) |

### Advanced Knowledge Base (rescoped 2026-08-14)

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 3.3.2 | Incremental Update | Knowledge Base/RAG | New document incremental indexing without full rebuild | LlamaIndex IngestionPipeline |
| 3.3.3 | Knowledge Quality Evaluation | Knowledge Base/RAG | Retrieval accuracy, recall, faithfulness evaluation | Ragas |
| 3.4.1 | Batch Document Indexing | Knowledge Base/RAG | Large-scale document set parallel parsing, chunking, indexing | LlamaIndex (IngestionPipeline) |

> **Deferred to P5 (2026-08-14)**: 3.1.2 OCR, 3.1.3 Table Extraction, 3.1.4 Layout Analysis — Docling/Unstructured/RAGFlow/Palantir DocInt have industrialized document parsing; Hecate should integrate, not build. 3.4.2 High-Throughput Retrieval — Qdrant native sharding + replication; a deployment guide covers it.

### Knowledge Graph — deferred to P5 (2026-08-14)

> 3.5.1 Knowledge Graph Construction, 3.5.2 Graph Database Integration, 3.5.3 Community Detection & Summarization — Microsoft GraphRAG + LlamaIndex PropertyGraph are mature open source; KG construction is a specialized domain. Integrate when a KG use case lands. Full rows preserved in the P5 deferred section.

### Multi-Agent Enhancement

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 2.8 | Collaborative Conflict Handling ✅ | Multi-Agent Orchestration | Conflict detection, locking, and resolution when multiple Agents access shared resources. 4 strategies: LAST_WRITE_WINS, HUMAN_APPROVAL, DISTRIBUTED_LOCK, NEGOTIATION. A2A task and permission conflict detection. | OpenClaw (session lanes) |
| 2.9 | Unified Skill Registry ✅ | Multi-Agent Orchestration | Unify Tools, Knowledge Bases, Workflows, and sub-Agents as attachable "Skills". SkillRef/ResolvedSkill abstraction, SkillRegistry service with resolve/invoke/format_for_llm. Agent.skill_ids JSON field. Resolves tools, KBs, workflows, agents, and remote A2A agents uniformly. Backward compatible with legacy fields. | Copilot Studio (Tools + Topics + Knowledge), Agentforce (Actions + subagents), watsonx (Agent + Agentic Workflow), Coze (Bot + Workflow/Chatflow), ADK (LlmAgent + WorkflowAgent) |
| 2.9a | Agent-Workflow Mutual Embedding ✅ | Multi-Agent Orchestration | Agent can invoke Workflow as a Tool (Coze: Bot mounts Workflow as skill). Workflow can embed Agent as a DAG node with shared context (watsonx: Agent Node in Agentic Workflow). Supports recursive nesting with `max_nesting_depth=3` limit (IBM anti-pattern guidance) and session/channel state passthrough. WorkflowTool class wraps workflows as callable tools. EnginePort.workflow_execute() optional method. Requires Canvas UI support for dragging Agent nodes into Workflows. | Coze (Bot + Workflow), watsonx (Agent + Workflow DAG), Agentforce (Subagent + Actions), ADK (LlmAgent as sub_agent of WorkflowAgent), IBM watsonx (nesting depth warning) |
| 2.10 | A2A Protocol ✅ | Multi-Agent Orchestration | Google Agent-to-Agent protocol (Linux Foundation v1.0): standardized HTTP+JSON/gRPC messaging between agents across frameworks. AgentCard discovery (`/.well-known/agent-card.json`), task lifecycle (submitted→working→completed/cancelled), artifact exchange, push notifications. A2A server (JSON-RPC handler, SSE streaming, DatabaseTaskStore, auth), A2A client (send_message, get_task, cancel_task, send_streaming_message, AgentCard discovery, push webhook). Enables cross-platform agent interoperability — Hecate agents can invoke external A2A agents and be consumed by external platforms. Security schemes: APIKey, HTTPAuth. Integration with existing EnginePort, guardrails, and tracing. | Google A2A, ADK (A2A client/server), Salesforce (cross-org A2A), IBM (A2A integration) |
| 2.10a | Signed Agent Cards ✅ | Multi-Agent Orchestration | Cryptographic signatures on Agent Cards for identity verification (A2A v1.0). ES256 key pair generation (ECDSA P-256), JWS signing with RFC 8785 JSON Canonicalization Scheme, JWKS served at `/.well-known/jwks.json`, key rotation with grace period, signature verification with algorithm pinning (ES256 only), JWKS caching with configurable TTL. Receiving agents verify card was issued by domain owner, preventing card impersonation attacks. Part of A2A v1.0 enterprise security requirements. **Planned enhancement (SS6)**: Multi-Agent Trust Verification. | OpenAI Agents SDK (handoff_description), Google ADK (agent.description), AutoGen Swarm, LangGraph (Command(goto=...)) |
| 2.10b | Multi-Agent Trust Verification | Multi-Agent Orchestration | Cross-agent trust verification in multi-agent workflows: (1) **Trust scoring** — each agent in a workflow has a trust score (0-100) based on signed Agent Card (2.10a ✅), historical behavior audit (9.14 ✅), and evaluation results (7.2a ✅); (2) **Capability attestation** — agents declare capabilities via signed claims, verified against actual behavior using 9.14 Structured Audit Pipeline data; (3) **Delegation depth limit** — max chain depth for `agent_execute` delegations (default: 3) prevents infinite trust chains; (4) **Revocation** — compromised agents can be revoked via JWKS removal or signed revocation list, propagating to all relying agents within cache TTL; (5) **Per-step trust gate** — TrustGateHook (PreLLMHook) blocks downstream agents from acting on outputs of upstream agents below trust threshold. Distinct from 9.4 (single-agent tool approval) — this is inter-agent trust in multi-agent graphs. **Depends on**: 2.10 ✅ A2A Protocol, 2.10a ✅ Signed Agent Cards, 9.14 ✅ Structured Security Audit Pipeline, 7.2a ✅ Built-in Evaluators. | Palantir AIP (5-layer governance: Context/Query/Logic/Action/Governance), Bedrock AgentCore (Cedar policy at Gateway boundary, cross-agent IAM), Salesforce (Einstein Trust Layer), Claude Code (permission scope inheritance), OpenAI Agents SDK (handoff guardrails) |

### Canvas UI Embedding (rescoped 2026-08-14)

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 1.1.24 | Human Input / Form Node | Agent Development | First-class canvas node type for structured human-in-the-loop interaction. Visual form designer: text fields, dropdowns, date pickers, file upload, multi-select. Configurable approval routing (approve/reject/modify → different downstream branches). Wraps engine-level interrupt() as a drag-and-drop node — no code required. | Dify (Human Input node), Coze (approval workflow), Qianfan (information collection node) |
| 1.1.25 | Trigger Node | Agent Development | Visual entry-point node types for event-driven workflows: Webhook Trigger (HTTP POST → workflow start), Schedule Trigger (cron/interval → workflow start), Event Trigger (message queue / event dispatcher → workflow start), MCP Trigger (MCP resource change → workflow start). Replaces implicit __start__ with explicit, configurable trigger nodes visible on canvas. | Dify (webhook/schedule/plugin event trigger), Coze (trigger system), Zapier (trigger-based workflows) |

> **Deferred to P5 (2026-08-14)**: 1.1.18 Agent-Workflow Canvas Embedding, 1.1.19 Unified Skill Selector, 1.1.20 Nested Graph Visualization — Canvas enhancements without user feedback are pure guessing; Dify's collaborative editing (Loro CRDT) is the more advanced direction to aim for when triggered.

### Memory Enhancement

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 4.4 | Knowledge Memory (L4) ✅ | Memory System | Long-term knowledge archive, searchable | Letta (Archival Memory) |
| 4.6 | Memory Isolation ✅ | Memory System | User/Agent/session-level memory isolation under multi-tenant | Mem0 (user_id + agent_id + run_id) |
| 4.3a | Memory Engine Enhancement | Memory System | Real-time/async dual-path processing (real-time summarization + async refinement), procedural memory (skill and workflow knowledge), graph-structured memory (entity relationship graph), memory self-evolution | AgentArts AgentBase (formerly Versatile) (4 memory types + dual-path processing) |
| 4.14 | Memory Importance Scoring | Memory System | Score memories by importance: access frequency, time decay, explicit user marking, semantic relevance. High-importance memories prioritized in retrieval. Enables memory ranking and automatic cleanup of low-value memories. | Mem0 (importance scoring), Letta (memory ranking) |
| 4.15 | Multi-Signal Fusion Retrieval | Memory System | Combine multiple retrieval signals: vector similarity, time decay, importance score, access frequency. Weighted fusion ranking for memory retrieval. Surpasses pure vector-only retrieval quality. | Mem0 (multi-signal fusion), Oxagen (hybrid vector+graph retrieval) |
| 4.16 | LLM-Managed Memory | Memory System | Agent autonomously decides when to store/retrieve/evict memories via function calls. ContextEngine provides memory_store/memory_search/memory_delete tools. LLM self-manages memory lifecycle instead of relying on system rules. | MemGPT/Letta (self-managed memory), Letta (core memory tools) |
| 4.17 | Memory Pressure Alert | Memory System | Insert system message when context approaches token threshold (default 70%), notifying LLM to trigger memory consolidation (summarize + archive + evict). Integrates with 4.10 Token Budget Governance. | MemGPT/Letta (memory pressure alerts), OS virtual memory (page fault) |
| 4.25 | Layered Memory System | Memory System | Three-layer memory architecture: Short-term (messages table, existing), Long-term (environment memory/ directory, new), Semantic (embeddings via Qdrant, existing). Memory Flush Middleware extracts facts from conversation prefix before context compression. Long-term memory stored as daily logs (memory/YYYY-MM-DD.md) + consolidated MEMORY.md. Environment-scoped isolation per agent. **Depends on**: 1.3.15 Agent Environment. | AgentScope (3-layer: Context/Short-term/Long-term, MemoryFlushMiddleware), Salesforce Agentic Memory (structured records + confidence scoring), AgentArts (4 memory strategies: Summary/Semantic/UserPreference/Episodic), Bedrock (FileSessionManager + session storage) |
| 4.21 | Task Memory | Memory System | Learn from task execution trajectories: record success/failure patterns, execution steps, tool selection strategies. Agent automatically retrieves relevant experience when executing similar tasks. **Planned enhancement (KM6)**: Work Context Graph — evolve Task Memory into a structured work memory graph: nodes for methods tried (success/failure), user corrections, source reliability ratings, and outcome quality scores. Agent starts each task with a fresh map of what's likely to work based on past work. Self-improving: every completed task enriches the graph. Follows Perplexity Brain's "remember what the agent did" pattern (+25% correctness, -13% cost). | AgentScope ReMe TaskMemory, Perplexity Brain (work memory, self-improving context graph) |

### Multi-Channel Access & Enhancement

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 11.2 | Web Widget (Simplified) | Multi-Channel Access | Embedded web chat component — **simplified version (Wave 1, in P3)** = 内部 Portal / embeddable for any Hecate deployment（直接复用现有 `(dashboard)/chat` + 员工 JWT 登录），S。架构决策：**Web Widget 不走 ChannelABC**——浏览器直接调 `/v1/chat/completions`，与 IM 渠道（webhook 推送）是不同的抽象层。**完整版（公开匿名场景：WidgetModel + 临时 JWT 签发 + Origin 白名单 + RS256 + JS bundle）**——Deferred to P5（按需触发：to-C / 公开网站场景，trigger = first explicit customer demand），M。 | Coze, Dify, Intercom (JWT + bundle optimization), Salesforce Enhanced Web Chat (RS256 + identityToken API), Google Dialogflow Messenger (Web Component) |
| 11.3 | Feishu (Lark) ✅ | Multi-Channel Access | Feishu bot SDK integration. **Status (2026-08-13, Delivered ✅)**: 首个 ChannelABC 真实实现——暴露当前 SPI 类型擦除（`raw: object`）等问题并推动 SPI 演进；webhook 签名验证 + tenant_access_token + 互动卡片。Wave 2 中国 IM（11.4/11.5）复用此模式，工作量 S each. | OpenClaw (Feishu channel), Hermes Agent (multi-platform gateway pattern), AgentScope (channels/feishu) |
| 11.4 | WeCom (WeChat Work) | Multi-Channel Access | WeCom bot SDK integration. **Status (2026-08-13, P5 deferred)**: 复用 11.3 模式（webhook + signature verification + 企业 IM 消息模板），S each. Trigger = 第一个明确要企微的中国客户需求。 | OpenClaw (WeCom channel) |
| 11.5 | DingTalk | Multi-Channel Access | DingTalk bot SDK integration. **Status (2026-08-13, P5 deferred)**: 复用 11.3 模式，S each. Trigger = 第一个明确要钉钉的中国客户需求。 | OpenClaw (DingTalk channel) |
| 11.8 | Intent Recognition & Routing | Multi-Channel Access | Unified entry-level intent recognition and dispatch, accurately parse user instructions and auto-route to the best Agent or workflow | AgentArts AgentSpace (formerly Versatile) (intent framework), AgentArts (multi-level intent recognition) |
| 11.9 | Slack ✅ / Discord/Telegram | Multi-Channel Access | International channels. **Status (2026-08-13)**: Slack（M，ChannelABC 第二个实现，验证 SPI 通用性）**已交付 ✅**；**Discord/Telegram（各 S）— Deferred to P5**（按需触发：trigger = 第一个明确要 Discord/Telegram 的国际客户需求）。 | OpenClaw (20+ channels), Hermes Agent (multi-platform gateway pattern) |

### Deployment & Operations

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 13.1 | SaaS Deployment | Deployment & Operations | Multi-tenant SaaS deployment mode: managed Hecate cloud + customer-managed VPC deployment. Includes production Helm chart with Ingress/RBAC/NetworkPolicy/ServiceAccount/PodSecurity standards, multi-environment values files (dev/staging/prod), secret management integration (Vault/SM). **This is the production chart home** — 13.4 K8s Scaling Test Harness produces CI-only chart; 13.1 promotes it to production grade with enterprise requirements. Includes CI/CD pipeline templates (ArgoCD/Flux), GitOps workflow, multi-region active-passive topology. **Depends on**: 13.2 ✅ Self-Hosted Deployment, 13.4 ✅ K8s Scaling Test Harness, 13.5 ✅ Data Backup & Recovery, 13.6 ✅ Version Upgrade. | Dify (official Helm 0.4.0 + production prod values), LangGraph (langchain-ai/helm), Salesforce Agentforce (managed cloud + Customer 360), Palantir Apollo (multi-env deployment), Bedrock AgentCore (AWS managed) |
| 13.1a | Canary Release | Deployment & Operations | Agent runtime version management, weighted routing canary (e.g., 70/30 split) | AgentArts (canary release) |
| 13.1b | Agent Identity Service | Deployment & Operations | Agent independent credential management, inbound/outbound authentication, secure inter-service communication | AgentArts (AgentIdentity) |
| 13.4 | Horizontal Scaling | Deployment & Operations | Stateless service scaling, load balancing; Session stateless design (rebuild runtime from persistent storage on each request) | K8s native, RelayAgent (Session V2 stateless) |
| 13.4a | Distributed Session State Store (Redis) ✅ (5/5) | Deployment & Operations | Redis-backed hot-path session state cache for multi-replica horizontal scaling. Any replica can pull down any session's full state snapshot (Channel values + Checkpoint + EventStore position) in sub-millisecond. RedisAgentStateStore pattern — local JSON file for development, Redis for production. Eliminates sticky-session requirement; any K8s replica with HPA can serve any user. Checkpoint persistence stays in PostgreSQL (durable); Redis is a hot-path cache layered on top. **Progress (2026-08-05)**: Change 1/5 (engine abstraction, 2026-08-01), Change 2/5 (Redis/PostgreSQL/Tiered implementations + factory, 2026-08-02), Change 3/5 (production wiring: WorkflowExecutionService checkpoint_store DI, chat.py Depends injection, lifespan singleton), Change 4/5 (horizontal-scaling validation: session locks, jitter retry, OTel observability, perf benchmarks, streaming save fix) and Change 5/5 (EventStore PG wiring: PostgresEventStore + factory + FastAPI DI + _sync_event_position + retention deferred) completed. **RESOLVED (deprecation)**: Change 6 deprecation implemented in 13.4a-6 (Aug 2026); hard removal in 13.4a-7 (≥ next minor). AgentStateStore ABC deprecated in 13.4a-6; migration guide at docs/migrations/agent-state-store.md. K8s scaling test harness deferred to 13.4 Horizontal Scaling. | AgentScope 2.0 (RedisAgentStateStore), AgentArts (fka Versatile) (sandbox snapshot → Redis/object store) |
| 13.4b | K8s Scaling Test Enhancements | Deployment & Operations | L2/L3 K8s scaling validation deferred from 13.4 minimal P0 unlock. Three additional chainsaw e2e tests: (1) pod restart session recovery — kill pod mid-multi-turn-chat, assert session state recoverable from other replicas; (2) readiness drain — SIGTERM triggers 503 drain, in-flight requests complete (2xx), new requests route to healthy pods; (3) cross-replica state strong consistency — same `(org_id, user_id, session_id)` across N replicas returns identical `agent_state`. locust multi-user load scenarios (ChatSession/SessionResume/ReadOnly) replacing simple wrk/hey CPU spikers. AgentScope-inspired startup validation — `WorkflowExecutionService.__init__` rejects `SESSION_STATE_STORE_BACKEND=memory` + `replicas > 1` combination at boot (avoid silent misconfiguration). PgBouncer actual deployment + 13.4a-4 perf benchmarks re-run under real K8s network (p95 < 50ms with pod-to-pod overhead, vs in-process 10ms). **Depends on**: 13.4 ✅ K8s Scaling Test Harness. **Status (2026-08-12)**: Deferred — engine-side stateless design completed via 13.4a 5/5 (locks + jitter retry + OTel + perf benchmarks cover main race conditions under fakeredis + SQLite); K8s-side validation (chainsaw e2e + locust + PgBouncer + perf re-run under real pod-to-pod network) bundled with 13.1 SaaS Deployment, which is the natural trigger (13.1's production Helm chart needs the CI-only chart as foundation; PgBouncer pool sizing and p95 latency targets need real load to be meaningful, not parameter guessing in vacuum). | LangGraph production guide (4-test e2e suite), AgentScope 2.0 (IllegalStateException on local-state + multi-replica), knext/cilium/k0s (nightly e2e patterns), Dify (PDB + multi-replica checklist) |
| 13.5 | Data Backup & Recovery ✅ | Deployment & Operations | Automated data backup and recovery: full/incremental backup scheduling, backup retention policies, point-in-time recovery, cross-region backup replication. Supports PostgreSQL, Redis, and object storage backups. **Planned enhancement (O12)**: Backup & Restore Management Console — UI console for scheduling backups, viewing backup history, performing restore operations, monitoring backup health, and testing recovery procedures. | Enterprise standard, K8s Velero |
| 13.6 | Version Upgrade ✅ | Deployment & Operations | Zero-downtime version upgrade management: rolling upgrade with health checks, upgrade preflight validation, rollback capability, database migration automation, feature flag gating. Supports blue-green deployment pattern with traffic shifting. **Implemented (Aug 2026)**: Health check three-endpoint pattern (`/health/live`, `/health/ready`, `/health/startup`) + SIGTERM graceful shutdown with readiness 503 drain; independent `hecate-migrate` binary extracted from app startup; Alembic `lock_timeout` safety net + expand-contract autogenerate (`process_revision_directives` hook); two-tier feature flag system (boot-time `FeatureSettings` + runtime `FeatureFlagModel` with Redis cache, lifecycle state machine, REST API); feature flag AST audit tool (`hecate flag-audit --check`); preflight check CLI + REST API; rollback runbook + docker-compose blue-green template. **Planned enhancement (EF4)**: Multi-Region Data Sovereignty — region-aware deployment with data residency controls. Region-pinned databases, vector stores, and log streams. Cross-region data movement requires explicit policy gate approval. GDPR Article 44 / PIPL / DPDP compliance through architectural guarantees, not contractual promises. | Enterprise standard, AgentAnywhere Sovereign (7 regions), BLACKBOX (US/EU residency) |

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
| 8.1a | Distributed Tracing ✅ | Observability & Operations | Trace → Span hierarchical tracing with OpenTelemetry-compatible context propagation. Enhanced: failoverReason and confidence_score as span attributes; auto-emission without manual instrumentation | LangFuse (Session > Trace > Observation), Microsoft Agent 365 (auto-OTel), OpenClaw (failover observability) |
| 8.1b | Metrics Collection ✅ | Observability & Operations | Request-level and token-level metrics collection for runtime monitoring | LangFuse, LiteLLM |
| 8.1c | Structured Logging ✅ | Observability & Operations | Structured JSON logging with correlation IDs for log aggregation and analysis | Enterprise standard |
| 8.20 | Run Replay & Debug Dashboard (NEW — 2026-08-14) | Observability & Operations | Given runId, timeline-replay complete execution: each superstep's channel changes, tool calls, LLM request/response, guardrail hook results. Built on EventStore (enriched by 1.3.19) + OTel spans; web UI with DAG visualization. Phase 1 = execution replay (P3). Phase 2 = version binding (data + code + eval version per trace, Palantir standard) — deferred to P5. **Depends on**: 1.3.19. **Seam notes (1.3.19 已交付)**: 纯消费方、零 schema 改动——消费冻结请求 / 值级 delta / `STEP_END` / SUBGRAPH 引用对 / `trace_id` 与 OTel JOIN；**回放覆盖范围 = Pregel 路径**（services 层直接写库的旁路不在回放语义内）。 | OMA offline Run Viewer, Conductor web dashboard, Salesforce Session Trace OTel API, deer-flow LLM Space (inspect + replay) |

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
| 13.10 | Temporal Conflict Resolution ✅ | Deployment & Operations | Conflict resolution for concurrent channel updates with 4 strategies: LAST_WRITE_WINS, MERGE_LIST, MERGE_MAP, HUMAN_APPROVAL. **Planned enhancement (E4)**: Saga/Compensation Pattern — multi-step workflow rollback when step N fails; automatically execute compensation actions for steps 1 through N-1. Leverages existing Temporal integration to provide saga-level durability (automatic retry with compensation logic, long-running workflow support, and step-level rollback). | Temporal, CRDT |

### Resilience & Safety

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 1.3.5f | Platform-Level Tool Gating ✅ | Agent Runtime | Tool definitions support `available_when` conditional expressions evaluated at Worker level — LLM cannot see or invoke tools whose conditions are unmet; hard platform gate, not prompt instruction | Salesforce Agentforce (`available when`), Microsoft Copilot Studio (action gating) |
| 1.3.5g | Unified Exception Hierarchy ✅ | Agent Runtime | `HecateError` base class → `EngineError` / `ChannelError` / `SecurityError` (Hecate-specific errors only); `ErrorCategory` enum for LLM/Tool error classification (replaces full LLMError/ToolError exception tree — 10-platform research shows no platform wraps provider exceptions); `ErrorClassifier` upgraded from string matching to isinstance-based type matching; replaces ad-hoc if-else HTTP status matching with structured catch | Google ADK (`ToolErrorType` enum), LangGraph (graph errors), LangChain (dual-inheritance mapping), OpenAI SDK (status-code hierarchy), Salesforce (failover + circuit breaker) |
| 1.3.5h | Framework-Level Auto-Retry ✅ | Agent Runtime | RetryStrategy ABC + NoRetryStrategy + RetryExecutor (non-streaming + stream-safe retry) + DefaultRetryStrategy (ErrorClassifier + exponential backoff with jitter) + PregelRuntime integration with per-node config override + EventStore observability | Google ADK 2.0 (`RetryConfig`), IBM watsonx (virtual policy retry) |
| 1.3.18 | Dynamic Orchestration (NEW — 2026-08-14) | Agent Runtime | 7th multi-agent pattern: coordinator node transforms goal + agent roster into a runtime task DAG, dispatches workers, synthesizes results. Built on Pregel — coordinator is a special node type emitting sub-graphs at runtime; parallel fan-out via channels; result folding before synthesis. Complements the 6 static collaboration patterns (2.7a). | Claude Code (dynamic workflows GA), MAF Magentic 1.0, OMA coordinator, AgentScope agent_spawn/agent_send |
| 1.3.19 | Event-Sourced Execution State / Log-as-Truth ✅ | Agent Runtime | **Shipped (2026-08-15, see [ADR-030](../design/adr/030-event-sourced-execution-state.md))**. EventStore upgraded from observation log to state carrier: WAL 序（`append_batch` 先于 channel apply）+ `STEP_END`/`INTERRUPT` 提交点（撕裂尾部回退）；fold 经 `ChannelBehavior.write` 裁决（裁决后事实，`CHANNEL_WRITE_REJECTED` 为审计伴生）；`LogPolicy` 黑名单（`_route` 豁免）+ `LogInvariants` 运行时不变式注册表（STEP.BOUNDARY / DISPATCH.TREE，fail-stop）；checkpoint 降级为物化缓存（`channel_state + log_version`，节奏 = turn 结束 / interrupt / 每 N superstep）；resume 日志推导校验（懒建 Session 行）；会话级 TTL 清扫（30d/7d，interrupted 豁免，级联删除）；`PostgresCheckpointStore` 软废弃。**远期方向注记**：日志截断/压缩（EventStoreDB TruncateBefore / Kafka compaction，物化缓存 `log_version` 锚点已预留语义）；archive 归档层（MinIO/S3 冷存，策略枚举已留位）。**Depends on**: 8.8 EventStore ✅, CheckpointStore ✅. | dsh session log (source-verified v0.1.0-rc.5), deer-flow DeltaChannel (v2.0.0), MAF Durable extension, OMA checkpoint+resume |

### AIP Capabilities (AgentArts[formerly Versatile]/Palantir-Inspired)

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 6.16 | NL2Agent / NL2Flow | Agent Development | Interactive clarification of development intent, auto-generate Agent configuration or workflow diagram, shorten end-to-end development cycle by 50% | AgentArts AgentStudio (formerly Versatile) |
| 6.18 | Trace Annotation | Observability & Operations | User thumbs up/down feedback annotation, Trace data multi-dimensional label classification, one-click addition to evaluation set | AgentArts AgentOps (formerly Versatile) |
| 11.16 | Per-Token-Type Auth Pipeline | Access Channel | Separate authentication pipelines for different token types (JWT, API Key, PAT, OAuth SSO) with edition gating. Each token type has distinct prepare/verification steps routed at gateway level. | Dify (per-token-type auth), Salesforce (ECA), Palantir (ECA) |
| 11.17 | Two-Tier Identity Model | Access Channel | Distinguish App-level identity (API Key for application) from User-level identity (JWT for end-user). Enables granular access control and audit at both application and user levels. | Dify (app + user identification), Salesforce (bypassUser), Palantir (bypassUser) |

> **Dropped (2026-08-14)**: 6.17 DSL Conversion Framework — MCP/A2A protocol standardization plus Salesforce open-sourcing Agent Script indicates the industry is converging on standard agent definitions, not DSL compatibility layers. See "Dropped Features" appendix.
>
> **Deferred to P5 (2026-08-14)**: 6.21 Decision Lineage — full decision lineage requires an Ontology foundation (data + function + app version binding per trace, Palantir standard); effort was underestimated in the initial analysis. Full row preserved in the P5 deferred section.

### Platform SPI (Core Infrastructure)

> **Architecture Principle**: Core capabilities (security, multi-tenant, local-deployment, basic eval) are native first-class. Extension capabilities (channels, evaluators, auth providers, notifiers, i18n) are pluggable via SPI (Service Provider Interface). All downstream plugins depend on Plugin SPI Core (5.5a).

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 5.5a | Plugin SPI Core ✅ | Platform SPI | Plugin registration, discovery, lifecycle management, and sandbox isolation ABC. Defines PluginRegistry, PluginManifest, PluginLifecycle. Foundation for all other SPIs — Channel/Evaluator/Auth/Notifier plugins register through this. Deprioritizes 5.5 (full Plugin System) which builds on this core. | OpenClaw (100+ extensions), HermesAgent (plugin hooks) |
| 7.2-abc | EvaluatorABC ✅ | Platform SPI | Evaluator uniform interface: `evaluate(input, context) → EvalResult`. Existing 40+ evaluators (7.2a) become `BuiltinEvaluator` implementations; third-party evaluators register via Plugin SPI. Elevates evaluation from hardcoded services to pluggable architecture. | Ragas (evaluator abstraction), Google ADK (evaluation metrics) |
| 11.1-abc | ChannelABC ✅ | Platform SPI | Channel adapter uniform interface: `receive`, `respond`, `stream`. REST/WS/CLI (11.1, 11.7) become `BuiltinChannel` implementations; Feishu/DingTalk/Slack etc. register as plugins rather than hardwired adapters. NotifierABC (8.6-abc) merged into ChannelABC — notification dispatchers become outbound Channel adapters registered via Plugin SPI. | OpenClaw (channel plugin architecture), Coze (channel SDK) |
| 10.3-abc | AuthProviderABC ✅ | Platform SPI | Auth provider uniform interface: `authenticate(token, db) → AuthContext | None`. JWT/APIKey (10.6) become `BuiltinAuthProvider` implementations; LDAP/OIDC/SAML register as plugins. SCIM directory sync (10.3b) extends this interface. | Enterprise standard (SAML/OIDC), Bisheng (SSO + LDAP) |
| 8.6-abc | NotifierABC 🔀 | Platform SPI | Notifier uniform interface merged into ChannelABC (11.1-abc). Mail/Webhook become `NotificationChannelAdapter` built-in implementations; PagerDuty/Slack Bot/DingTalk notifications register as Channel plugins. | PagerDuty API, Enterprise standard |

### Internationalization

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 15.1 | i18n SPI ✅ | Internationalization | Locale passing (Accept-Language header → runtime locale context), message catalog loading mechanism (JSON/YAML), fallback chain (requested → user → workspace → English), parameter interpolation, plugin translation declarations. Translation files are pluggable assets — Core ships English only, community contributes other languages via Plugin SPI. No multi-language built into Core. | Django i18n, FastAPI babel |

### Ops Center & Operations

> Unified administrative control plane consolidating monitoring, evaluation, deployment, cost governance, and compliance into a single operator interface.

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 8.9 ✅ | Unified Ops Center Dashboard | Observability & Operations | Centralized admin console homepage aggregating all operational views: system health summary, active alert count, evaluation pass rates, cost trends, deployment status, recent audit events. Role-based dashboard personalization with configurable widget layout. **Planned enhancement (O8)**: Custom Dashboard Builder — drag-and-drop dashboard editor allowing operators to create personalized operational views with flexible chart types. | Palantir Control Panel, Salesforce Agentforce Studio, Microsoft Power Platform Admin Center |
| 8.9a ✅ | Agent Health Monitoring Dashboard | Observability & Operations | Per-agent health monitoring with near real-time metrics: uptime, error rate, average latency, escalation rate, user satisfaction score. Agent fleet overview with health status indicators (healthy/warning/critical), drill-down to individual agent session traces. Configurable health score formula with SLA breach detection. | Salesforce Agentforce Health Monitoring, Microsoft Agent 365 |
| 8.9b ✅ | Conversation Analytics & Quality Scoring | Observability & Operations | Conversation analytics dashboard: session volume trends, user satisfaction scores, conversation clustering by intent, topic distribution analysis, quality score computation. Root cause analysis for low-quality conversations with drill-down to individual turns. **Planned enhancement (OE5)**: Conversation Topic Clustering & Systematic Low-Score Analysis. | Salesforce Agentforce, LangFuse (custom dashboards) |
| 8.9c ✅ | Tool Execution Analytics Dashboard | Observability & Operations | Per-tool execution metrics dashboard: latency percentiles (p50/p95/p99), success/failure rate, error distribution by type, invocation count over time, token cost per tool call. Tool usage heatmaps showing which tools are called most frequently by which agents. | Salesforce (Session Trace OTel API), Palantir AIP (end-to-end observability) |
| 8.10 | CI/CD Evaluation Gating | Observability & Operations | Evaluation results integrated with CI/CD deployment pipeline. Evaluation score regression automatically blocks deployment. Supports Git PR-triggered evaluation with baseline comparison. Configurable regression tolerance thresholds per evaluator. | Braintrust (CI/CD eval gating), LangSmith (evaluation in deployment pipeline), Promptfoo |
| 8.12 | Agent Catalog Governance & Quality Gateway | Observability & Operations | Agent registration to managed catalog requires quality evaluation gateway. Pre-publish quality assessment: journey completion rate, tool call accuracy, instruction adherence, answer relevancy, safety metrics. Quality score computation with configurable pass thresholds. | IBM watsonx.governance, Salesforce Agentforce (agent quality scoring) |
| 9.10 | Outbound DLP Engine ✅ (2026-08-10) | Security & Compliance | Outbound Data Loss Prevention for AI agent workflows — scans data before it leaves to external LLM providers, MCP servers, or third-party APIs. Three scan points: Pre-LLM, Post-Tool, Pre-Memory. Two enforcement modes: Redact and Block. Cross-request entropy tracking detects slow-drip exfiltration. **Implemented (Aug 2026)**: `DLPService` (recognizers: regex, entropy, custom; scanners compose recognizer pipelines), `PolicyResolver` (rule precedence, allow/deny/redact), per-environment `DLPConfigModel` (PostgreSQL), MCP egress filter on `MCPClient` outbound payloads, Guardrail Hooks integration (Pre-LLM Hook on LLMWorker + Post-Tool Hook on ToolWorker), audit pipeline wiring (ToolDecisionModel + SecurityFinding). REST API: `GET/POST /admin/dlp/config`, `GET /admin/dlp/findings`, `POST /admin/dlp/scan` (ad-hoc). 50+ built-in recognizers (credit card, SSN, email, phone, JWT, AWS keys, etc.). | Control Zero (Gateway DLP), Pipelock (62 patterns), ORION Security (agentic DLP) |
| 9.11 | Agent Runtime Protection | Security & Compliance | Stateful runtime security monitoring across the agent execution trajectory. Five detector types: Goal Drift, Tool Chain Escalation, Memory Poisoning, Behavioral Anomaly, Rogue Agent. Session state persists across supersteps within PregelRuntime. | SafeAgent, AgentShield, Cisco AI Defense, OWASP ASI01/06/08/10 |
| 7.10 | Automated Continuous Red Teaming | Evaluation & Testing | CI/CD-integrated automated adversarial testing for AI agents. 50+ vulnerability types across 7 categories. Multi-turn adversarial workflows. Community threat intelligence integration. **Planned enhancement (OE10)**: Adversarial Test Generation — LLM auto-generates boundary cases. | Lakera Red, Promptfoo, AJAR, DeepTeam, IBM watsonx.governance |
| 13.17 | Environment Management & ALM Pipeline | Deployment & Operations | Multi-environment lifecycle management (DEV/STAGING/PROD) with environment promotion workflows, approval gates, and audit trail. Solution packaging and import/export across environments. | Microsoft Copilot Studio, Salesforce CLI deployment |
| 13.18 | API Management & Developer Portal | Deployment & Operations | Centralized API key management with scoped permissions, usage analytics per API key, rate limit monitoring and configuration. Developer portal with interactive API documentation, testing playground, SDK download, and webhook configuration. | Enterprise standard (Apigee, Kong), Dify |

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
| 10.7 | Budget Management & Cost Governance ✅ | Multi-Tenant & Enterprise | Platform-wide budget management: per-org, per-workspace, and per-agent spending limits with hard/soft cap enforcement. Monthly budget alerts, cost forecasting, chargeback reports. | Salesforce Digital Wallet, Microsoft Copilot Credits |
| 10.8 | Enterprise Vault Integration ✅ | Multi-Tenant & Enterprise | Integration with enterprise secret management platforms: HashiCorp Vault, AWS Secrets Manager, Azure Key Vault, Google Secret Manager. Per-agent identity authentication to vault via OAuth 2.0 token exchange. Dynamic short-lived credentials replace static API keys. SecretProviderABC abstracts backend differences. | HashiCorp Vault, AWS Secrets Manager, Azure Key Vault |

### Tool Platform

> Plugin management, MCP connection, and tool platform infrastructure.

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 5.5b ✅ | Plugin Packaging & Distribution | Tools & Plugins | Plugin packaging format (`.hecate-plugin` bundle), packaging CLI (`hecate plugin package`), upload/install/uninstall UI, version management + upgrade workflow, installed plugin registry (beyond 5.5's local directory scan — handles packaged bundle extraction and registration). Marketplace distribution foundation (for P5 Asset Marketplace 12.0). **Depends on**: 5.5 (runtime engine) + TP5 (type ABCs for typed packaging). **Does NOT include**: marketplace UI (P5 12.0), plugin signing/security (P5 5.13). | Claude Code Plugins (directory-based), OpenClaw (ClawHub marketplace + npm-pack), Dify (plugin marketplace + remote debugging) |

### P3 Dependency Chain

```
SSO/Quota → Evaluation (7.2a–7.6b) → Security (9.2a–9.8) → Observability (8.2–8.6)
Resilience → Exception Hierarchy (1.3.5g) → Auto-Retry (1.3.5h) → Tool Gating (1.3.5f)
Plugin SPI Core (5.5a) → EvaluatorABC (7.2-abc) + ChannelABC (11.1-abc) + AuthProviderABC (10.3-abc) + NotifierABC (8.6-abc)
Plugin SPI Core (5.5a) → i18n SPI (15.1)
Multi-Channel → Web Widget → Feishu/WeCom/DingTalk/WeChat → Intent Recognition (all channels via ChannelABC)
Failure Analysis (7.7a–c) → Meta-Agent Ops (13.9a–d)
Model Management → Cost Tracking → Multi-Auth → Key Security → Model Classification → Model Catalog (6.44) ✅ → Model Lifecycle (6.45) ✅
MCP Gateway → Plugin System (5.5, depends on 5.5a) → Tool Permission (available_when) → Tool Caching
MCP Server → MCP Connection Management (5.4d) → Plugin Packaging (5.5b)
Advanced KB → Incremental Update → Batch Indexing (OCR/Table/Layout deferred P5, 2026-08-14)
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
EventStore → Decision Lineage (6.21, P5 deferred 2026-08-14) → Decision Audit → Compliance
EventStore → Event-Sourced Execution State (1.3.19) → Run Replay (8.20) + Projection Registry (8.21, P4)
Pregel + Collaboration Patterns (2.7a ✅) → Dynamic Orchestration (1.3.18) → runtime task DAG
Skill Loading (5.9 ✅) → Skill Provider Registry (5.9 enhancement) → community skills ecosystem
Built-in Tools (5.1 ✅) → Browser Automation (6.27) → Computer-use (6.27a, P4)
Auth Service → Per-Token-Type Auth (11.16) → Two-Tier Identity (11.17)
Canvas → Human Input/Form Node (1.1.24) → Trigger Node (1.1.25) → Event-Driven Workflows
CheckpointStore → Distributed Session State Store (13.4a) ✅ (5/5) → Horizontal Scaling (13.4)
```

---

## P4: Intelligent (Months 15-18)

> Self-learning, Hallucination Detection, Agentic RL, Prompt Self-Optimization, Ontology Actions, OAG, Agentic RAG, GraphRAG, Memory Integration, Temporal Memory, Lazy GraphRAG, SDK/CLI development, NL2X, distributed orchestration, intelligent routing, deep research, 5-Level Intent, Object Logs, Simulation, Computer-use (6.27a), DataAgent, VibeCoding, Peer Selection, Agent Teams, Distributed Team Orchestration, **ACP (2.13)**, **Projection Registry (8.21)**, **Atomic File Locks (13.20)**, **Voice Agent Pipeline (11.11)**. *(2026-08-14 re-scope: Browser Automation moved to P3 as 6.27; Knowledge Graph API 3.5.5 + Extended Document Processing 3.1.8 deferred to P5.)*

### Self-Learning & Evolution

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 1.3.5e | Hallucination Detection & Mitigation | Agent Runtime | PostLLMHook-based claim extraction → evidence retrieval via EnginePort.knowledge_query → confidence scoring (0-1) → block/warn/pass action; optional multi-agent voting (Judge & Jury pattern) for high-stakes scenarios; confidence metadata exposed in API responses | Salesforce (Judge & Jury), OpenClaw (community anti-hallucination skill), Google ADK (hallucinations_v1 evaluator) |
| 1.3.5i ✅ | Deterministic Hooks (Lifecycle Events) | Agent Runtime | Model-independent lifecycle event handlers (PreToolUse, PostToolUse, PreEdit, PostEdit, Stop) configured in settings. Shell commands, not AI. Auto-format on edit, lint on save, block dangerous commands. Complements Guardrail Hooks (AI-interception) with deterministic control. **Planned enhancement (E3, upgraded 2026-08-14)**: Ordered Waterfall Middleware Chain — upgrade the 4 flat Guardrail Hook types into an ordered responsibility chain with `next()` delegation (listener must call `next()` or the chain short-circuits): agent/pre-step, agent/request, agent/request-error, tools/pre-execute, tools/execute (around-wrapper), tools/post-execute, tools/result — scope-filtered per agent (dsh waterfall semantics, source-verified). The 4 existing hooks become stages in this chain. Coverage references (updated 2026-08-14): deer-flow middleware chain — 9 middlewares in released 2.0.0 (ThreadData/Uploads/Sandbox/Summarization/TodoList/Title/Memory/ViewImage/Clarification), grown to 12+ items on main incl. TokenBudgetMiddleware + declarative layered builder (2.1.0 Unreleased); AgentScope middleware — Java 5-stage onion (onAgent/onReasoning/onActing/onModelCall/onSystemPrompt) + order() since 2.0.1, Python now 7 hook positions with `on_check_permission` added in 2.0.6 (2026-08-07) wrapping PermissionEngine before on_acting. Design implication: Hecate's chain includes an explicit permission stage and an order() API. Sequenced after 1.3.19 in Sprint 7 (middleware events consume the enriched log). **Seam notes (1.3.19 已交付)**: 增量加中间件阶段事件类型、事件经语义层 schema；**路径 A/C 收敛** —— tool-loop 建模为引擎内子图（与 E3 配对，`SUBGRAPH_START/END` 引用对已就位，运行时 wiring 延后到 follow-up，见 [ADR-030](../design/adr/030-event-sourced-execution-state.md)）。 **Planned enhancement (TP4)**: Session-level events (SessionStart, SessionEnd, UserPromptSubmit, PreCompact) and tool name matchers — regex-based filtering (e.g., `mcp__github__.*`) that targets specific tools for PreToolUse/PostToolUse hooks. Enables per-tool hook configurations without global side effects. | Claude Code Hooks (Sep 2025, 12 events + tool matchers), Salesforce (before_reasoning/after_reasoning), AgentScope (6 middleware hooks) |
| 1.3.15 ✅ | Agent Environment | Agent Runtime | Unified agent execution environment abstraction. AgentEnvironment ABC with LocalEnvironment (filesystem) and PostgreSQLEnvironment (production) implementations. EnvironmentManager for lifecycle management (create/get/close with TTL eviction). Data model separates AgentState (volatile, per-session: conversation buffer, compressed summary, tool call history) from Environment (durable, per-agent: sessions/, memory/, files/, skills/). Integrates with existing session/conversation system and provides environment-scoped file management. **Naming rationale**: distinct from WorkspaceModel (multi-tenant isolation boundary); "Environment" describes the agent's execution context without conceptual collision. **Depends on**: Session Management ✅, Context Engine ✅. | Bedrock AgentCore (Agent Runtime + Session Storage), AgentScope (Workspace: Local/Docker/E2B), Dify (AgentRuntimeSession + AgentDrive), Claude Code (Working Directory + SessionStore) |
| 1.3.16 ✅ | Agent State Separation | Agent Runtime | Separate volatile AgentState (per-session: conversation buffer, compressed summary, permission context, tool/task sub-contexts) from durable Environment (per-agent: session logs, memory, files). AgentStateStore persists state snapshots independently from environment filesystem. Enables cross-process/cross-machine session resume when backed by distributed store (Redis/JDBC). Context compression mutates AgentState in memory; Session writes at end of call. **Depends on**: 1.3.15 Agent Environment ✅. | AgentScope (AgentState vs Workspace separation, AgentStateStore), Bedrock (session storage vs BYO file system), Claude Code (SessionStore adapter: S3/Redis/Postgres) |
| 1.3.15a ✅ | Environment Backend: Docker | Agent Runtime | DockerEnvironment backend implementation for AgentEnvironment — isolates agent file system into Docker containers with persistent volumes. Each agent gets its own container with bind-mounted filesystem (sessions/, files/, memory/, skills/). Adds exec_shell to AgentEnvironment ABC for shell command execution inside container. EnvironmentManager refactored to support backend selection (local/docker) with warm pool for container reuse. MCP gateway deferred to follow-up. **Upgrade path**: 6.32 gVisor Enhanced Sandbox → 6.32a Kata Containers → 6.40 Firecracker microVM Backend. **Depends on**: 1.3.15 Agent Environment ✅. | AgentScope (DockerWorkspace), DeerFlow (AioSandboxProvider), Bedrock AgentCore (microVM per session), Claude Code (container-based sandboxing) |
| 1.3.15b ✅ | Context Offloading | Agent Runtime | Offload oversized conversation context and large tool results to environment persistent storage. Context messages exceeding token threshold are written to environment as files, replaced in-context with a read_file pointer. Agent reads full content on demand via file tools. Prevents context window overflow without losing information. **Depends on**: 1.3.15 Agent Environment, 4.13 Context Engine Processor Chain. | AgentScope (offload_context + offload_tool_result, Offloader protocol), Claude Code (5-layer compaction pipeline, tool result truncation) |
| 1.3.15c ✅ | Sandbox Environment Mount | Agent Runtime | Mount environment into sandbox container — agent's files, tools, and skills are directly accessible inside the sandbox at `/mnt/env`. Stop/resume cycles persist: sandbox terminates, environment data flushes to durable storage, new sandbox mounts same storage on resume. Execute commands (shell, tests, builds) operate on the same files as the agent. **Depends on**: 1.3.15 Agent Environment, 9.4c Docker Sandbox Executor. | Bedrock AgentCore (managed session storage at `/mnt/workspace`, 14-day TTL, stop/resume), AgentScope (DockerWorkspace bind-mount), Claude Code (self-hosted sandbox per session) |
| 1.3.17 ✅ | Agent Invocation Mode | Agent Runtime | Upgrades `AgentExecutionPort.agent_execute()` to full LLM pipeline parity with LLMWorker: tool loading from AgentModel, knowledge base retrieval, PreLLMHook/PostLLMHook guardrails, context assembly, token budget management. Adds `invocation_mode` field to AGENT node DSL config supporting `"direct"` (default, inline execution via port) and `"tool"` (expose agent as callable tool via AgentDefinition for hierarchical delegation). AgentWorker routes to AgentTool registration when mode=tool. AgentDefinition.resolve_tools() applies whitelist/blacklist filtering. **Depends on**: 1.3.1 ReAct Agent Loop ✅, 5.1 Built-in Tools ✅, Agent-as-Tool Pattern 2.3d ✅. | OpenAI Agents SDK (invocation patterns), LangGraph (subgraph invocation), Coze (Bot mounts Workflow), watsonx (Agent Node) |
| 1.3.6 | Self-Learning Agent Runtime | Agent Runtime | Automated end-to-end evolution cycle: trajectory analysis, policy evolution, constraint generation, constraint injection, validation, feedback; scheduled execution via Meta-Agent Scheduler | HermesAgent (closed learning loop), openJiuwen (agent_evolving) |
| 1.3.6a | Trajectory Analysis ✅ | Agent Runtime | Analyzes conversation trajectories to extract success/failure patterns using rule-based heuristics (timeout detection, tool usage patterns, confidence scoring) | HermesAgent (closed learning loop), openJiuwen (agent_evolving) |
| 1.3.6b | Policy Evolution ✅ | Agent Runtime | Adjusts tool priorities and prompt effectiveness scores based on trajectory analysis results; supports tool priority boost/penalty (±0.1) | openJiuwen (textual gradient optimization) |
| 1.3.6c | Evolution Integration ✅ | Agent Runtime | Bridges evidence tracking with trajectory analysis and policy evolution into a unified self-improvement pipeline | HermesAgent (Curator) |
| 1.3.6d | Synthetic Environment Generation ✅ | Agent Runtime | Generates synthetic training environments with configurable difficulty (20-60% success rate band) and tool availability for agent evaluation | openJiuwen (auto_harness) |
| 1.3.6e | Self-Evolution Closed Loop | Agent Runtime | Full closed-loop self-evolution: Trajectory Analysis → Policy Evolution → Constraint Generation → Constraint Injection → Validation → Feedback | HermesAgent (closed learning loop), openJiuwen (agent_evolving) |
| 1.3.10 | Multi-Level Intent Recognition | Agent Runtime | Hierarchical intent recognition: atomic intent (single user query) → workflow intent (multi-turn complex task) → session intent (overall dialogue goal) | AgentArts (multi-agent controller), intent recognition |

### Agentic AI (Moved from P3)

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 6.15 | Agentic RL Framework | Agent Intelligence | Data flywheel: trace collection → labeling → RL training → model update. Async RL framework, reward mechanisms (rule-based, generative, credit assignment), interaction environments, optimization algorithms. | AgentArts Agent Self-Optimization (formerly Versatile) |
| 6.19 | Prompt Self-Optimization | Agent Intelligence | ACE/GEPA algorithm-based automatic prompt optimization against evaluation datasets, multi-round self-iteration to maximize effect metrics | AgentArts AgentStudio (formerly Versatile) |
| 6.20 | Ontology Action System | Knowledge Base/RAG | Define Actions (operations that modify objects/write back to systems), Agent executes via Action Tool. Supports manual/auto execution modes with pre-execution approval. | Palantir AIP Actions, AgentArts (fka Versatile) Ontology Orchestration |
| 6.22 | OAG (Ontology-Augmented Generation) | Knowledge Base/RAG | RAG + Logic + Actions complete closed loop. LLM not only retrieves knowledge but also reasons and executes actions, writing back to source systems. | Palantir OAG |

### Distributed Orchestration

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 2.5 | Peer Selection (Selector) | Multi-Agent Orchestration | LLM selects the next speaker in multi-agent deliberation | AutoGen SelectorGroupChat |
| 2.11 | Agent Team Templates | Multi-Agent Orchestration | Pre-built multi-agent team patterns (Debate, Research, Code Review, Brainstorm, Hierarchical Task); reusable team configurations with role definitions, interaction protocols, and termination conditions | openJiuwen (agent_teams), AutoGen (Team patterns) |
| 2.13 | ACP (Agent Client Protocol) Support (NEW — 2026-08-14) | Multi-Agent Orchestration | External coding agents (Claude Code, Codex, Gemini CLI) as worker nodes in Hecate orchestration. Subagent provider seam (in-process / fork / ACP) with multiple coexisting providers. Complements A2A (agent-to-agent) — ACP is host-to-coding-agent. **Depends on**: 2.10 A2A ✅. | OMA (ACP support), Trinity (multi-runtime: Claude Code/Codex/Gemini CLI per agent), deer-flow (Claude Code ACP adapter), dsh (subagent seam, source-verified) |
| 13.15 | Distributed Team Orchestration | Multi-Agent Orchestration | Cross-process agent team formation: agent discovery, registration (capability advertisement), Redis-backed cross-session EventBus, remote task allocation; builds on P2 EventBus/TaskAllocator ABCs and P3 A2A Protocol (2.10) with ZMQ/gRPC transport | openJiuwen (spawn_member + remote bootstrap), Temporal, Google A2A |
| 1.3.11 | Asynchronous Execution API Mode | Agent Runtime | Third execution mode for long-running workflows (minutes to days): submit workflow → receive task_id immediately → poll status endpoint or subscribe to webhook for completion. Complements existing sync (blocking) and streaming (SSE) modes. Eliminates client-side timeout risk for complex multi-step workflows (batch processing, report generation, multi-round research). Task lifecycle: submitted → running → completed/failed/cancelled. Supports cancellation via DELETE on task_id. | Coze (3 API modes: sync 5min/streaming 15min/async 24h), Dify (Celery worker-based async execution + task ID polling) |

### Context Engineering Enhancement

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 4.13 | Context Engine Processor Chain | Memory System | Evolve ContextEngine from fixed 3-method ABC (select/compress/estimate) to pluggable processor pipeline: ToolResultTruncationProcessor, RoundWindowProcessor, CompressionProcessor, KVCacheAwareProcessor. Non-destructive context projection with offload/reload capability. Inspired by openJiuwen ContextProcessor chain + AgentScope ContextConfig + Claude Code 5-level cascade. **Planned enhancement (2026-08-14)**: LLM-managed compaction via surface replacement — compaction replaces a history range with one summary node while original events stay in the log (shadowing); compaction events are themselves checkpoint sources; integrates with 1.3.19 log-as-truth. | openJiuwen (ContextProcessor chain), AgentScope (ContextConfig + Offloader), Claude Code (5-level compression cascade), dsh CompactionEngine + surface replacement (source-verified), deer-flow TokenBudgetMiddleware |

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
| 2.6a | Multi-Agent Central Controller | Multi-Agent Orchestration | Intent recognition routing to sub-workflows, supports global intent, default workflow, orchestration visualization | AgentArts (multi-agent controller) |

### Canvas UI Orchestration

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 1.1.21 | Multi-Agent Controller Canvas | Agent Development | Canvas UI for multi-agent controller: intent routing visualization (intent → workflow mapping), global intent config panel, start/default/end workflow assignment. Canvas UI for 2.6a backend. | AgentArts (multi-agent controller), Huawei Pangu (controller) |
| 1.1.22 | Orchestration Mode Switching | Agent Development | Canvas mode toggle: Sequential (linear chain), Parallel (fan-out/merge), Conditional (branch/merge), Intent Routing (controller pattern). Mode-specific node palette and connection rules. | CrewAI (process types), LangGraph (workflow agents) |
| 1.1.23 | Execution State Visualization | Agent Development | Real-time agent status on canvas during execution: which Agent is running (animated border), which edges are active (highlighted), error states (red), completion (green). Step-through debugging. **Planned enhancement (G5)**: Workflow Analytics Dashboard — per-workflow execution metrics (success rate, average duration, bottleneck nodes, frequent failure points) displayed inline in Studio. **Planned enhancement (G7)**: Agent Debug Inspector — superstep-level state inspector showing Channel values, node inputs/outputs, and execution timeline at each BSP barrier. | CrewForm (live execution), LangGraph (streaming visualization), LangGraph Studio (debug inspector) |
| 1.1.26 | Object CRUD Node | Agent Development | Ontology-aware canvas node types for Knowledge Graph operations: Create Entity, Update Entity, Delete Entity, Query Entity (Cypher/template), Create Relation, Traverse Subgraph. Each node maps to GraphStore ABC operations via EnginePort.knowledge_query. Type-safe parameter binding from ontology schema definitions — properties autocomplete from entity type metadata. Replaces generic Tool node wrapping with dedicated ontology development experience. | AgentArts (formerly Versatile, Object management/extraction nodes), Palantir AIP (OSDK typed CRUD) |
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
| 4.5 | Memory Integration | Memory System | Background auto-integration, deduplication, forgetting. **Planned enhancement (KM3)**: Sleep-time Memory Consolidation — scheduled background synthesis cycle that reviews conversation history during idle periods, extracts durable facts, updates memory blocks, and cleans stale entries. Configurable schedule (default: overnight). Follows Perplexity Brain's overnight synthesis and Letta's sleep-time compute pattern. Uses background subagents with isolated write access to memory store, producing a consolidated "learned context" that improves next-session starting point. | HermesAgent (Curator), CrewAI (auto-integration), Letta (sleep-time compute), Perplexity Brain (overnight synthesis) |
| 4.18 | Conversation Recall Storage | Memory System | Conversation history as dedicated memory layer with semantic search. Agent can search past conversations via conversation_search function. Similar to MemGPT's Recall Storage. | MemGPT/Letta (recall storage), Claude Code (conversation search) |
| 4.19 | Self-Editing Memory | Memory System | Agent can overwrite/correct stored memories via function calls. Memory version tracking to prevent accidental overwrites. Supports LLM-driven memory quality improvement. | MemGPT/Letta (self-editing memory), Letta (core memory write) |
| 4.20 | Multi-Step Memory Retrieval | Memory System | Support function chaining for multi-step memory queries. Agent can execute search → evaluate → re-search → synthesize in one query flow. Similar to MemGPT's heartbeat mechanism for complex retrieval. | MemGPT/Letta (function chaining, heartbeat) |
| 4.22 | Tool Memory | Memory System | Record tool usage experience: parameter tuning, common errors, best practices. Agent automatically retrieves relevant experience when calling tools, improving tool call accuracy. | AgentScope ReMe ToolMemory |
| 4.23 | Cross-Thread Memory Store | Memory System | Independent memory store for cross-thread data: user preferences, shared knowledge, cross-session facts. Complements Checkpoint (thread-scoped) with Store (cross-thread). | LangGraph Store (cross-thread persistence) |

### Tool Enhancement

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 5.4 | MCP Server | Tools & Plugins | Expose Hecate capabilities as MCP tools | OpenClaw (bidirectional MCP) |
| 5.9c | Skill Auto-Detection | Tools & Plugins | Skills auto-invoke based on task context matching (description field). No manual agent-skill association needed. Progressive disclosure: skill descriptions loaded at startup, full content loaded on demand. | Claude Code Skills 2.0 (Jan 2026) |
| 5.9d | Skill Versioning | Tools & Plugins | Independent version management for skills: version snapshots, rollback to previous versions, version diff comparison. Aligns with Resource Versioning (14.x) mechanism. Enables safe skill updates without breaking dependent agents. | Claude Code Skills (version tracking), OpenClaw (ClawHub versioning) |
| 5.9e | Skill Dependency Declaration | Tools & Plugins | Declare skill dependencies (skill A requires skill B). Dependency resolution at load time, circular dependency detection, version constraint support. Enables composable skill packages. | npm (dependency resolution), Python (package dependencies) |
| 5.12 | MCP Sandbox Security | Tools & Plugins | Sandboxed MCP tool execution with resource limits (CPU, memory, network), tool-level permission policies, and audit logging for external MCP server calls | openJiuwen (MCP sandbox), E2B |

### Model Enhancement

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 6.5 | Self-Hosted Inference ✅ | Model Management | Locally deployed models (vLLM/Ollama). Managed Model Deployment (G5) complete: InferenceBackendABC, OpenAICompatibleBackend, endpoint registration, periodic /health polling, Prometheus metrics collection, health-based routing. Hecate manages endpoint metadata — does NOT orchestrate inference server lifecycle. | vLLM (PagedAttention), Ollama, Salesforce BYOM |
| 6.6 | Model Fine-Tuning ✅ | Model Management | Fine-tune models on business data. Fine-Tuning Pipeline (G7) complete: FineTuningBackendABC, OpenAIFineTuningBackend, InMemoryFineTuningBackend (test stub), DatasetModel (upload/version/preview), FineTuningJobModel (async job lifecycle), FineTuningService orchestration, one-click deploy to ModelRegistryModel. | Baidu Qianfan (8+ fine-tuning methods), Bailian, Vertex AI Model Tuning |
| 6.14 | Intelligent Router with Caching | Model Management | Rule-based + LLM semantic routing with response caching (semantic similarity hit detection), automatic fallback chains, cost-aware routing optimization, and cache invalidation strategies | openJiuwen (IntelliRouter), LiteLLM (Router + caching) |

### Evaluation Enhancement

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 7.5 | A/B Testing (rescoped to Agent-Level, 2026-08-14) | Evaluation & Testing | Agent/version-level controlled experiments: split traffic across two agent versions (or two agent configurations), collect quality metrics, z-test statistical significance. Reuses 6.8a A/B Testing for Models machinery (traffic splitting + z-test) generalized from model routing to agent routing — platform-native capability external eval platforms cannot do (they don't own the serving path). Model-level comparison already covered by 6.8a ✅; prompt comparison delegated to evaluation-platform integration (LangSmith/Braintrust, per 7.6b drop rationale 2026-08-14). | Salesforce A/B Testing API (pilot, agent versions), 6.8a ✅ (z-test machinery) |
| 7.7 | Security Testing | Evaluation & Testing | Prompt injection detection, red team testing | NeMo Guardrails, Promptfoo |
| 7.8 | Agentic RL Optimization & Data Flywheel | Evaluation & Testing | Trace data backflow → auto-labeling → RL optimization → model/Prompt update, forming Agent self-evolving data flywheel | AgentArts AgentOps (formerly Versatile) (data flywheel + AgenticRL) |
| 7.9 | Testing Center / Sandbox | Evaluation & Testing | Dedicated testing UI for ad-hoc and batch agent testing: create test suites from production traces or synthetic data, run parallel evaluations across multiple agent configurations, view side-by-side result comparisons, regression detection. Supports sandboxed test execution isolated from production data. **Planned enhancement (OE1)**: CI/CD Evaluation Gating — evaluation results integrated with deployment pipeline; evaluation score regression automatically blocks deployment; supports Git PR-triggered evaluation. | Salesforce Testing Center, Dify sandbox mode, Palantir AIP Evals, Braintrust (CI/CD eval gating), LangSmith (deployment evaluation) |

### Security & Integration

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 9.3 | Retrieval Security | Security & Compliance | Retrieval result injection detection, knowledge base access control | NeMo Guardrails (retrieval rails) |
| 9.7 | Network Isolation | Security & Compliance | Agent execution network sandbox, outbound/inbound traffic control, domain whitelist/blacklist | openJiuwen (network isolation), Docker network |
| 9.16 | External Policy Engine Interface | Security & Compliance | Pluggable external policy engine interface (PolicyEngineABC). Built-in Python engine wraps existing ToolAccessPolicy as default. Adapter implementations: Cedar (via pycedar), OPA/Rego (via OPA sidecar HTTP API), **Dogwood** (AWS open-source 2026-08-06: Cedar superset + MFOTL temporal logic — policies over action *sequences*: prerequisites, ordering, rate limits, escalation approval; deny-by-default, forbid-overrides-permit preserved; built into Bedrock AgentCore Gateway with stateful event tracking), custom. Policy hot-reload with sub-second emergency override. Policy versioning + audit trail (which policy version made which decision). Externalizes security policy from application code for external auditability — security teams can review policy files without reading Python code. **Depends on**: 9.4 ✅ Execution Security, 5.14 Environment Security (P0). | Bedrock AgentCore (Cedar: forbid-wins, default-deny, SMT formal verification, NL→Cedar generation) + **Dogwood temporal policies (2026-08-06, Apache 2.0)**, DeerFlow (GuardrailProvider protocol + OAP Passport), HermesClaw (OPA + Rego), Zylos Research (hybrid: Cedar for structural auth + Casbin for hard limits + custom scoring for anomaly tracking) |
| 9.16a | Chaos Engineering for Multi-Replica | Security & Compliance | L3 chaos engineering for 13.4/13.4a multi-replica state store. Validates Redis SETNX lock + PG `SELECT FOR UPDATE` + tiered double-insurance under realistic failure modes: (1) chaos-mesh network partition between Hecate pod and Redis — assert lock acquire retry + fail-fast `SessionStateConflictError` propagates correctly, no silent dual-write; (2) toxiproxy latency injection between pod and PostgreSQL — assert `SELECT FOR UPDATE` row lock timeout + connection pool backpressure handling; (3) pod-kill during multi-turn chat — assert session recovery from PG/Redis durable state + no in-flight request loss; (4) node drain during HPA scale-up — assert PDB `minAvailable: 2` honored + drain completes within SIGTERM grace period. Runs as nightly chaos suite alongside 13.4b chainsaw tests. **Distinct from 9.x L0/L1/L2 security features** — this is L3 chaos (failure injection), not preventive control. **Depends on**: 13.4 ✅ K8s Scaling Test Harness, 13.4b ✅ K8s Scaling Test Enhancements, 9.4 ✅ Execution Security. | Netflix Chaos Monkey (chaos engineering origin), chaos-mesh (CNCF sandbox, network/pod/io failure injection), toxiproxy (Shopify, deterministic latency/timeout simulation), Litmus Chaos (CNCF, K8s-native chaos), cilium nightly scale-test (continue-on-error "failures reviewed, not gating") |
| 9.17 | AI Auto-Approval | Security & Compliance | AI-driven automatic approval for low-risk tool calls. ReviewerAgent evaluates tool call risk (argument safety, target path, network destination, historical pattern), auto-approves low-risk requests, escalates only high-risk to human. Configurable risk thresholds per workspace/agent. Reduces approval fatigue for autonomous workflows. Complements existing ApprovalCallback (ONCE/SESSION/PROJECT/GLOBAL scope). **Depends on**: 9.4 ✅ Execution Security, 9.14 Structured Audit Pipeline. | Codex CLI (auto_review: AI sub-agent reviews eligible approval requests), Claude Code (auto mode: classifier evaluates action safety with hard_deny/soft_deny/allow tiers) |
| 3.3.4 | Third-Party KB Integration | Knowledge Base/RAG | Import external knowledge bases (Confluence, SharePoint, Notion, Websites) | Unstructured, LlamaIndex connectors |

### Deep Research

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 5.11 | Deep Research | Tools & Plugins | Multi-round research agent: query decomposition, parallel web search, cross-source verification, structured report generation; configurable depth/breadth | OpenAI Deep Research, Google Deep Research, openJiuwen (deep research) |

### Enterprise Enhancement

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 10.3b | SCIM Directory Sync ✅ | Multi-Tenant & Enterprise | User/group auto-provisioning via SCIM 2.0 protocol. Syncs from Azure AD, Okta, OneLogin to Hecate org/workspace membership. Extends AuthProviderABC (10.3-abc) with `sync_identity` capability — delegated CRUD on identity provider. | SCIM RFC 7643/7644, Azure AD SCIM, Okta SCIM |

### Internationalization Enhancement

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 15.2 | Community Translations | Internationalization | Community-contributed translation file management: upload/review/version locale `.po`/`.json` files, locale coverage dashboard, missing-key detection. Built on i18n SPI (15.1) — translation files are pluggable assets, not Core code. | Django Rosetta, Crowdin |

### AIP Enterprise Capabilities (AgentArts[formerly Versatile]-Inspired)

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 6.23 | 5-Level Intent Recognition | Agent Intelligence | Atomic intent → workflow intent → session intent layered recognition, with intent dynamic caching and controller self-evolution for performance and effect balance | AgentArts AgentStudio (formerly Versatile) |
| 6.24 | Object Log & Decision Log | Observability & Operations | Record object event snapshots and decision behavior snapshots, unified audit center, object history and time-series analysis | AgentArts AgentBase (formerly Versatile) |
| 6.25 | Object History Analysis | Knowledge Base/RAG | View object evolution over time, support state replay, object timeline operations, and rollback analysis | AgentArts AgentBase (formerly Versatile) |
| 6.26 | Simulation Environment | Agent Intelligence | Ontology simulation environment isolation: simulation execution and reasoning without affecting real systems. Visual reasoning analysis, one-click process deployment. **Planned enhancement (E5)**: Checkpoint Branching for What-If Analysis — create new execution sessions from historical checkpoints with modified state, enabling parallel "what-if" scenario testing without affecting the original session. | AgentArts AgentBase (formerly Versatile), Palantir Scenario Staging, LangGraph (checkpoint branching) |
| 6.27a | Computer-use (NEW — split from 6.27, 2026-08-14) | Tools & Plugins | Agent operates computer GUI to execute tasks: open native apps, click through UI, verify changes (Claude Code computer-use research preview pattern). Browser half of the original 6.27 moved to P3 as Browser Automation Tool (6.27, Playwright-based). | Claude Code (computer use from terminal), Manus |
| 6.28 | DataAgent (NL2SQL) | Tools & Plugins | NL2SQL data query, data analysis script execution, chart generation, multi-data-warehouse tool integration | AgentArts AgentCapability (formerly Versatile) |
| 6.29 | VibeCoding Tool Set | Tools & Plugins | Claude Code-like CLI tool set: file IO, command execution, code execution, task management, file search | AgentArts AgentCapability (formerly Versatile) |
| 6.30 | Fine-Grained Permission Control | Security & Compliance | Object-level, attribute-level, row-level permission control, security policy fast mapping to business ontology orchestration engine | AgentArts AgentBase (formerly Versatile), Palantir Dynamic Security |
| 6.31 | Data Integration Framework | Deployment & Operations | Enterprise data source connectors (ERP/CRM/database), data pipeline orchestration, ETL/ELT support | AgentArts industrial integration (formerly misattributed "Versatile ET/OT Engine"), Palantir Foundry |
| 13.19 | Service Mesh Integration | Deployment & Operations | Istio or Linkerd service mesh for Hecate multi-replica K8s deployment. Provides: (1) mTLS between Hecate pods, Redis, PostgreSQL, MCP gateways — eliminates manual cert management; (2) east-west traffic policy (e.g., restrict tool-executor pods from calling external APIs directly); (3) observability — distributed tracing spans across pod boundaries (complements Hecate's existing OTel instrumentation with mesh-level traces); (4) advanced traffic management — canary routing for 13.1a Canary Release, shadow traffic for 6.8a A/B Testing, circuit breaking for 6.8c Per-Prefix Circuit Breaker. **Helm chart enhancement**: optional `serviceMesh.enabled` flag in `values.yaml` injects sidecar + creates DestinationRule/VirtualService resources. **Depends on**: 13.1 SaaS Deployment (production Helm chart), 13.4 ✅ K8s Scaling Test Harness (multi-replica baseline). | Istio (CNCF, market leader, ~75% production share), Linkerd (CNCF, lightweight, Rust-based), Consul Connect (HashiCorp), Cilium Service Mesh (eBPF-based, kernel-level observability), AWS App Mesh (Bedrock AgentCore uses it internally for multi-tenant isolation) |
| 6.32 | gVisor Enhanced Sandbox | Deployment & Operations | Docker + gVisor user-space kernel, VM-level isolation, ~200ms cold start, significant security improvement over pure Docker | E2B, OpenHands |
| 6.32a | Kata Containers Sandbox | Deployment & Operations | Docker-compatible VM-level isolation via Kata Containers runtime. Each container runs inside a lightweight VM with independent kernel, strong isolation for multi-tenant security. K8s-native, compatible with existing container orchestration. Fills isolation gap between gVisor (user-space kernel) and Firecracker (dedicated microVM). **Depends on**: Docker ✅ (1.3.15a). | Kata Containers (OpenStack), AWS Firecracker, Google gVisor |
| 6.33 | Decision Simulation | Agent Intelligence | Pre-execution simulation of Action side effects, verify safety, reasoning and optimization in simulation environment without affecting real system | AgentArts Simulation Execution (formerly Versatile) |
| 6.40 | Firecracker microVM Backend | Deployment & Operations | Firecracker microVM as AgentEnvironment fourth backend (after local/docker/gVisor/kata). ~125ms boot, <5MiB overhead per VM, KVM hardware-enforced kernel isolation. Each agent gets its own Linux kernel, filesystem, and network namespace. Supports up to 150 microVMs/sec/host. Suspend/resume with state preserved for up to 8 hours. Industry consensus (2026.02): Firecracker is the recommended isolation primitive for untrusted AI-generated code execution. **Depends on**: 1.3.15a ✅ DockerEnvironment, 6.32a Kata Containers. | AWS Lambda MicroVM (agent code execution foundation since 2018), 华为 AgentArts (microVM-based 安全沙箱), kubernetes-sigs/agent-sandbox (Kata+Firecracker integration) |
| 13.4c | Session-Level microVM Isolation (Paradigm B) | Deployment & Operations | **Paradigm shift** from Hecate's current model (Paradigm A: multi-replica pods + shared Redis/PG state + distributed locks) to Bedrock AgentCore model (Paradigm B: per-session Firecracker microVM + managed Memory service). Each chat session runs in its own microVM with isolated CPU/memory/filesystem — **eliminates dual-write race entirely** (no concurrent writers to same session), **eliminates Redis SETNX lock complexity**, **eliminates PgBouncer connection-pool exhaustion risk** (each session has its own PG connection). Trade-offs: resource overhead (each microVM ~5MiB + cold start ~125ms), 8h max lifetime (forced session rebuild), requires Memory service for cross-session persistence. **Architectural impact**: PregelRuntime needs refactoring from in-process execution to RPC-based microVM invocation; SessionStateStore becomes optional (state lives in microVM). **Distinct from 6.40** (which is sandbox-level microVM as AgentEnvironment backend): 13.4c is session-level isolation of the *entire agent runtime*, not just tool execution. **Trigger condition**: when noisy-neighbor problems emerge in production or enterprise customers demand Bedrock-equivalent isolation guarantees. **Depends on**: 6.40 ✅ Firecracker microVM Backend, PregelRuntime RPC refactor (new engine change), 13.4 ✅ K8s Scaling Test Harness (baseline for comparison). | Amazon Bedrock AgentCore (only production implementation of Paradigm B, per-session microVM + Memory service + 8h maxLifetime), Hecate 13.4a (Paradigm A baseline with Redis SETNX + PG FOR UPDATE), AgentScope 2.0 DistributedBackend (Paradigm A abstraction) |
| 6.41 | WASM Runtime Backend | Deployment & Operations | WebAssembly (WASI) as code execution backend for bounded compute and MCP tool components. Deny-by-default capability model — Wasm module is inert unless host explicitly passes capability imports. <1ms startup, <1MB memory overhead. Supports MCP-native Wasm Components loaded from OCI registries with signed manifests. Complements Firecracker (hardware isolation) for high-frequency, short-lived tool calls where VM overhead is prohibitive. **Depends on**: 5.3 ✅ MCP Client. | Microsoft Wassette (MCP-native Wasm runtime, Aug 2025), Cloudflare V8 Isolates (sub-ms startup), Extism/Wasmtime (WASI sandbox), kubernetes-sigs/agent-sandbox (WASM backend option) |
| 11.18 | Multi-Stream Modes | Access Channel | Support multiple stream output modes: values (full state after each superstep), updates (incremental diffs), messages (LLM token stream), debug (node events, channel changes). Clients can subscribe to multiple modes simultaneously. | LangGraph (4 stream modes: values, updates, messages, debug) |

### 2026-08-14 Re-scope Additions

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 8.21 | Projection Registry (NEW — 2026-08-14) | Observability & Operations | Generalized projection registry: any user-facing concept (permission presets, agent profiles, derived views) = named projection over the event log + canonical setter events; zero-database state derivation with consistent rebuild semantics. Permission presets become projections instead of DB tables. **Depends on**: 1.3.19 Event-Sourced Execution State (P3). **Seam notes (1.3.19 已交付)**: `derive_messages()` 为第一个投影函数；注册表是命名投影的泛化，fold 机器不动。 | dsh projection registry (Part 4 Mech 18, source-verified) |
| 13.20 | Atomic File Writes & Cross-Process File Locking (NEW — 2026-08-14) | Deployment & Operations | `<file>.lock` advisory locking + write-to-temp + atomic rename commit for multi-process file mutation (skills, plugin configs, state files); stale-lock recovery; ~150 LOC utility. Hard requirement once multi-replica pods mutate shared filesystem assets. | dsh storage layer (Part 8 Mech 81, source-verified) |
| 11.11 | Voice Agent Pipeline (moved from P5, 2026-08-14) | Multi-Channel Access | End-to-end voice pipeline: STT → agent workflow → TTS with configurable STT/TTS providers. Barge-in support (interrupt TTS playback when user speaks). Voice is becoming a standard channel (OpenAI/Hermes/Salesforce/IBM all ship it). | OpenAI Agents SDK (VoicePipeline + RealtimeAgent), Hermes (streaming TTS + barge-in + wake words), Salesforce Agentforce Voice, IBM ElevenLabs managed TTS |

### P4 Dependency Chain

```
SDK/CLI → Code Sandbox → Managed Runtime → NL2Agent → NL2Workflow
Self-Learning → Trajectory Analysis → Policy Evolution → Constraint Injection → Self-Evolution Closed Loop
Hallucination Detection → Self-Learning → Intent Recognition → Deep Research
Evaluation → Agentic RL Framework (6.15) → Data Flywheel → Model Optimization
Evaluation → Prompt Self-Optimization (6.19) → ACE/GEPA Algorithm → Auto-Optimized Prompts
Knowledge Graph (3.5.1, P5 deferred) → Ontology Action System (6.20) → Object Actions → Writeback
6.20 + RAG → OAG (6.22) → RAG + Logic + Actions Closed Loop
A2A Protocol (P3) → Peer Selection → Agent Team Templates → Distributed Team Orchestration
A2A Protocol (2.10 ✅) → ACP Support (2.13) → external coding agents as worker nodes
Event-Sourced State (1.3.19, P3) → Projection Registry (8.21) → derived state without DB
Multi-Agent → Expert Panel → Inter-Agent Comm → Central Controller
Central Controller → Controller Canvas → Orchestration Mode → Execution Visualization
Advanced RAG → Multi-Modal → GraphRAG → Reranking (Extended Document 3.1.8 deferred P5, 2026-08-14)
Knowledge Graph (3.5.1-3.5.3, P5 deferred) → GraphRAG Query Engine (3.5.4) → DRIFT Search (KM4) + Schema-Aware Traversal (KM5) → Lazy GraphRAG (3.5.14/KM2)
Memory Integration (4.5) → Sleep-time Consolidation (KM3) → Overnight Synthesis → Learned Context
Task Memory (4.21) → Work Context Graph (KM6) → Self-Improving Work Memory
Agent-Native Graph Memory (3.5.6) → Temporal Memory & Reasoning (3.5.13/KM1) → Time-Aware Retrieval
Tool Enhancement → MCP Server → MCP Sandbox Security
Deterministic Hooks (1.3.5i) → Session Events + Tool Matchers (TP4) → Per-Tool Hook Configuration
Agentic RAG (3.2.10) → Iterative Retrieval → Query Reformulation → Multi-Step Reasoning
GraphRAG Query Engine (3.5.4) → Global/Local/Hybrid Search → Multi-Granularity Retrieval
Knowledge Graph API (3.5.5, P5 deferred) → CRUD + Cypher Queries → Text-to-Cypher
Agent-Native Graph Memory (3.5.6) → Graph-Aware Context Assembly → Persistent Graph Memory
Conversation Recall Storage (4.18) → Semantic Search over History → Long-Term Context
Self-Editing Memory (4.19) → LLM-Driven Memory Correction → Memory Quality Improvement
Multi-Step Memory Retrieval (4.20) → Function Chaining → Complex Query Resolution
Tool Memory (4.22) → Tool Usage Learning → Parameter Tuning
Cross-Thread Memory Store (4.23) → Cross-Session Facts → Shared Knowledge
LLM → 5-Level Intent Recognition (6.23) → Controller Self-Evolution → Intent Caching
EventStore → Object Log & Decision Log (6.24) → Object History Analysis (6.25) → State Replay
Ontology Action System (6.20) → Decision Simulation (6.33) → Simulation Environment (6.26) → Safe Verification
LLM → Browser Automation (6.27, P3) → Computer-use (6.27a, P4) → GUI Automation
RAG + LLM → DataAgent (6.28) → NL2SQL + Data Analysis + Chart Generation
CLI Tools → VibeCoding (6.29) → File IO + Command Execution + Code Execution
Pregel Runtime → Multi-Stream Modes (11.18) → values/updates/messages/debug
Streaming → Asynchronous Execution API (1.3.11) → Long-Running Workflow Support
AuthProviderABC (P3) → SCIM Directory Sync (10.3b)
i18n SPI (P3) → Community Translations (15.2)
```

---

## P5: Ecosystem & Industry (Months 13-15)

> Industry capabilities, marketplace, compliance, desktop/mobile clients, distribution, end-user applications, template ecosystem.

### Asset Marketplace

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 12.0 | Asset Marketplace | Industry Capabilities | Unified asset discovery/subscription/installation entry, covering browse→try→install→configure workflow for six asset types: app templates, models, MCP, plugins, Prompts, Skills. **Planned enhancement (EC3)**: Semantic Marketplace Discovery — vector search by intent: user/agent describes what they need, semantic similarity matches best agent/tool/skill. Failed searches become demand signals (bounties) for supply side. **Planned enhancement (EC6)**: Governed Agent Catalog — agent listing requires governance approval workflow (submit → security scan → evaluation → approval → publish). Supports agents built with any framework. Cross-cloud, cross-system catalog with publisher identity verification and lifecycle governance. | AgentArts (asset marketplace), Coze (plugin marketplace), OpenClaw (ClawHub), Google (Agent Finder), IBM (Agent Catalog, governed, any framework) |

### Industry Capabilities

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 12.1 | Industry Templates | Industry Capabilities | Pre-built industry Agent templates (finance, healthcare, legal, education…) | AgentArts |
| 12.2 | Industry Knowledge Packs | Industry Capabilities | Pre-built industry knowledge bases (regulations, terminology, workflows…) | AgentArts |
| 12.3 | Industry Skill Packs | Industry Capabilities | Pre-built industry skills (report generation, data analysis, compliance checks…) | AgentArts (Skill system) |
| 12.4 | Industry Integration Guide | Industry Capabilities | Vertical industry quick-start guide and best practices | AgentArts (best practices) |
| 12.5 | Partner Monetization Infrastructure | Industry Capabilities | Partner program infrastructure for commercial ecosystem: (1) **Stripe payment integration** — in-app purchasing with credit card processing, invoicing, and automated payouts; (2) **Revenue sharing engine** — configurable split (e.g., 70/30) between platform and partner, automatic calculation per transaction; (3) **Unified billing** — consolidate all marketplace asset purchases (agents, tools, MCP servers, skills) into a single customer bill; (4) **Auto-provisioning** — purchased assets activated instantly, no manual setup; (5) **Partner GTM console** — product management, offer creation, invoice tracking, payout dashboard. Enables ISV partners to build, distribute, and monetize on Hecate marketplace. | Salesforce AgentExchange ($800M ARR, GTM app + Stripe + unified billing), IBM Agent Connect (ISV onboarding + sales channels), Huawei AI Model Partner Program (20+ model providers) |

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
| 3.5.12 | Ontology Versioning | Knowledge Base/RAG | Ontology version management: version snapshots, version diff, backward compatibility checks. Aligned with Resource Versioning (14.x) mechanism. | @ontograph-core (version management) |
| 8.1d | W3C Trace Context Propagation | Observability & Operations | Cross-agent trace context propagation via A2A protocol headers. Enables end-to-end observability in multi-agent workflows spanning different frameworks/organizations. Uses W3C Trace Context standard with OpenTelemetry. **Planned enhancement (OE6)**: Multi-Agent Distributed Tracing — end-to-end tracing across A2A agent calls with sub-agent execution visualization, cross-organization trace correlation, and agent-to-agent latency breakdown in trace timeline. | A2A v1.0 (2026), OpenTelemetry, W3C Trace Context, OpenTelemetry GenAI Agent Spans |
| 7.8a | Agent Benchmark Integration | Evaluation & Testing | Integration with industry-standard agent benchmarks: SWE-bench (software engineering), AgentBench (general), τ-bench (tool use). Automated evaluation against standardized tasks. | SWE-bench (Princeton), AgentBench (THUDM), LangGraph (benchmark integration) |

### Memory Management

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 4.24 | Memory Versioning | Memory System | Memory version management: version snapshots, version diff, rollback capability. Enables memory history tracing and recovery from accidental overwrites. | Mem0, Letta |

### Multi-Modal & Desktop

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 11.11a | Vision Input (Multi-Modal) | Multi-Channel Access | Image understanding for screenshots, document OCR, UI analysis, chart interpretation. First step toward multi-modal agent. Leverages LLM vision capabilities (GPT-4o, Claude 3.5 Sonnet). | Google ADK (multi-modal), Salesforce (visual grounding), OpenClaw (image tools) |
| 11.12 | Desktop Client | Multi-Channel Access | Windows/macOS native client, local file processing, Office document operations | AgentArts (desktop client) |
| 11.13 | AgentSpace SDK (Embedded Integration) | Multi-Channel Access | Layered SDK from lightweight UI components to deep integration APIs, enabling rapid embedding of Agent capabilities in existing enterprise applications. **Planned enhancement (EC5)**: Cross-Surface Experience Layer — define agent behavior and interactive UI components once, deploy natively across all channels (web, mobile, Slack, Teams, voice, ChatGPT-compatible surfaces). Separates agent logic from delivery surface, eliminating per-channel UI/security/data rebuild. Includes adaptive rendering: agent responses adapt to surface constraints (voice = TTS, mobile = cards, web = rich UI). Follows Salesforce AXL Headless Experience Layer pattern. | AgentArts AgentSpace (formerly Versatile) (SDK integration), Salesforce AXL (define once, deploy everywhere) |

### Distribution & End-User Access

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 13.14 | PyPI Package Distribution | Deployment & Operations | pip-installable SDK (hecate-sdk) published to PyPI; programmatic Agent creation, workflow definition, and execution without UI; CLI tools for agent management. **Planned enhancement (EC4)**: Community Agent Gallery — community members publish `agent.json` (agent config + system prompt + examples) to a shared gallery. Other users one-click install/try/fork. Public Trace Gallery shares interesting execution trajectories. Agent Harness Registry lists compatible tools and runtimes. Follows Hugging Face tiny-agents pattern. | openJiuwen (PyPI: openjiuwen), CrewAI (pip install crewai), Hugging Face (tiny-agents collection + `agent.json`) |
| 13.16 | Edge/Lite Deployment | Deployment & Operations | Lightweight single-binary deployment option for edge/on-device scenarios. Minimal dependencies (SQLite + local embedding). Enables running agents on developer laptops, IoT devices, or air-gapped environments. **Planned enhancement (EF6)**: Confidential Computing Mode — HYOK (Hold Your Own Key) encryption with customer-managed keys, data capsules for process-level isolation, confidential inference via local models (Ollama sidecar) with zero outbound data flow. Air-gapped mode flips `AIRGAPPED=true` refusing all outbound calls. For defense, healthcare, and financial high-compliance scenarios. | Claude Code (local), Cloudflare Agents (edge), Vercel AI SDK, Huawei AgentArts (HYOK + data capsules), AiSOC (air-gapped + Ollama) |
| 2.12 | Agent Payments (AP2) | Multi-Agent Orchestration | Google Agent Payments Protocol (AP2) for agent-to-agent financial transactions. Extension of A2A protocol with payment primitives. Enables autonomous commerce between agents with human consent gates. | Google + Coinbase (Sep 2025), A2A extension |
| 11.14 | End-User Application | Multi-Channel Access | User-facing web application (agent-studio style) with conversation interface, agent discovery, knowledge base browsing, and session management; separate from admin/developer console | openJiuwen (agent-studio), Coze (user app) |
| 11.15 | Mobile GUI | Multi-Channel Access | Mobile-responsive admin/chat interface for agent management and conversation on iOS/Android; supports push notifications and offline message queuing | openJiuwen (mobile GUI), Coze (mobile) |
| 11.6 | WeChat Official Account / Mini Program | Multi-Channel Access | WeChat ecosystem integration (订阅号 / 服务号 / Mini Program). **Status (2026-08-12, P5 deferred)**: to-C 场景，不是 Hecate to-B 主线。Trigger = 第一个 to-C / 微信公众号场景的明确客户需求。S. | Coze (WeChat channel) |
| 11.10 | Custom Channel SDK | Multi-Channel Access | SDK-extensible custom channels（让用户自建渠道适配器）。**Status (2026-08-12, P5 deferred)**: 长尾 niche，trigger = 社区或客户明确请求"自建渠道"。ChannelABC SPI 在 Wave 1/2 实战后沉淀的稳定接口再开放给外部，避免 SPI 频繁变动。 | OpenClaw (channel plugin architecture) |
| 11.2 (full) | Web Widget (Full — anonymous to-C) | Multi-Channel Access | Full version of Web Widget for anonymous公开场景。**Status (2026-08-12, P5 deferred)**: WidgetModel + 临时 JWT 签发 + Origin 白名单 + JWT RS256 + JS bundle（参考 Intercom bundle 优化、Salesforce RS256、IBM watsonx 双 RSA 密钥、Google CX Agent Studio token broker 模式、Dialogflow Messenger Web Component 形态）。**Trigger = 第一个 to-C 公开网站场景（自部署用户做营销 / 客服 / Lead 收集）的明确客户需求**——11.2 简化版（P3 Wave 1）只覆盖内部 Portal / embeddable，不覆盖公开匿名。架构复用：浏览器直接调 `/v1/chat/completions`（不走 ChannelABC）。 | Intercom (JWT + bundle optimization), Salesforce Enhanced Web Chat (RS256 + identityToken API), IBM watsonx (双 RSA 密钥对 + 加密 payload), Google CX Agent Studio (token broker + reCAPTCHA), Dialogflow Messenger (Web Component) |

### AIP Advanced Capabilities (AgentArts[formerly Versatile]/Palantir-Inspired)

| # | Feature | Domain | Description | References |
|---|---------|--------|-------------|------------|
| 6.34 | AI Office | Tools & Plugins | AI generation and processing of PPT, Word, Excel documents with template parsing | AgentArts AgentCapability (formerly Versatile) |
| 6.35 | Industrial Data Integration | Deployment & Operations | MQTT + OPC UA protocol access, OT data fusion engine for industrial IoT data *(Reference note 2026-08-14: previously attributed to "Versatile Industrial Data Graph Platform" — "Versatile" was Huawei's agent platform renamed AgentArts (智果) in Feb 2026, which has no such named module; reference corrected to generic industrial-IoT practice)* | MQTT/OPC UA ecosystems, industrial IoT platforms |
| 6.36 | Asset Marketplace Operations | Industry Capabilities | Agent/MCP/plugin asset lifecycle: publish → use → precipitate. Asset operation incentive mechanism, partner monetization via cloud store | AgentArts Asset Marketplace (formerly Versatile AgentGallery) |
| 5.13 | Plugin Security & Signing | Tools & Plugins | End-to-end plugin security lifecycle for marketplace distribution: (1) **Plugin signing** — cryptographic signing with Ed25519 keys, signed manifest in `plugin.yaml`, key registry for publisher identity verification; (2) **Security scanning** — pre-publication automated scans: static analysis (code patterns, API surface), dependency vulnerability check (CVE database), secret detection (API keys, tokens in code), permission audit (requested vs declared capabilities); (3) **Digest verification** — SHA-256 digest verification on install, tamper detection, rollback on verification failure; (4) **Security score** — 0-100 rating per plugin based on scan results, displayed in marketplace, configurable minimum score threshold for organizational install policy. Integrates with Asset Marketplace (12.0) and Compliance & Audit Center (9.9). | OpenClaw (ClawPack signed manifest + digest verification), ClawHub (security scan summaries, SkillScan), npm (package signing), PyPI (signed packages) |
| 6.37 | Memory Clustering & Conflict Resolution | Memory System | Memory clustering and graph-structured generation, memory conflict processing, traceability to source messages | AgentArts Memory Engine (formerly Versatile) |
| 6.38 | Self-Planning (PDDL + MCTS) | Agent Intelligence | PDDL formal expression + Monte Carlo search tree + Tree/Graph of Thought, ultra-long task planning with 100+ execution steps | AgentArts Agent Self-Optimization (formerly Versatile) |
| 6.39 | Tool Auto-Creation | Tools & Plugins | Agent automatically creates new tools based on task requirements without human intervention | AgentArts Agent Self-Optimization (formerly Versatile) |
| 6.41b | Cloud Document Connector | Knowledge Base/RAG | Block structuring, custom templates, AI preprocessing, data/relationship/summary extraction from cloud documents | AgentArts AgentBase (formerly Versatile) |
| 6.42 | Global Branching | Knowledge Base/RAG | Ontology zero-downtime evolution, branch-isolated development and testing | Palantir Global Branching |
| 6.43 | Embedded Ontology | Deployment & Operations | Lightweight ontology for edge devices, supporting offline decision-making | AgentArts Edge Deployment (formerly Versatile), Palantir Embedded Ontology |
| 11.19 | Platform-Level Governance | Access Channel | Unified enforcement layer for identity + data + API + AI trust policies. 20+ governance policies including auto API key rotation, JWT auth, real-time sensitive content blocking, model routing, fallback. | Salesforce (MuleSoft Flex Gateway), Palantir (Trust Layer) |
| 11.20 | Zero Trust Architecture | Access Channel | IAM-based service accounts with principle of least privilege. Token exchange for identity propagation (OAuth 2.0 Token Exchange RFC 8693). Per-agent unique identity with scoped permissions. | Google ADK (IAM + service accounts), Salesforce (Zero Trust) |
| 14.1 | Agentic Resource Discovery (ARD) | Ecosystem | Support for the Agentic Resource Discovery open specification (Google + Microsoft + Hugging Face, June 2026): (1) **Catalog publishing** — publish `ai-catalog.json` on Hecate's domain (like `robots.txt` for agents), listing all discoverable agents, skills, MCP servers, and tools; (2) **Federated registry integration** — Hecate catalogs are crawlable by external registries (Google Agent Registry, Hugging Face Discover), and Hecate can crawl/index external catalogs; (3) **Runtime capability discovery** — agents discover external capabilities at runtime by querying ARD registries with intent descriptions, receiving verified publisher metadata; (4) **Trust verification** — publisher identity verified via domain ownership and cryptographic signatures before establishing direct connection. Complements A2A AgentCard (per-agent discovery) with catalog-level standard. | Google ARD spec + Agent Registry, Hugging Face Discover Tool (reference implementation), Microsoft, broad industry participation |

### Deferred from P3/P4 (2026-08-14 competitor-analysis re-scope)

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
| 6.13 | Model Management UI Redesign | Model Management | Three-level page structure, breadcrumb nav, dual-panel testing layout. **Trigger**: same as 6.9 (UI without users is speculative — Dify spent $30M on UI). | AgentArts (three-level nav) |
| 1.1.18 | Agent-Workflow Canvas Embedding | Agent Development | Drag Agent into Workflow canvas / Workflow as Tool node; recursive nesting. **Trigger**: user feedback; aim at Dify Loro CRDT collaborative editing direction. | Coze (Bot + Workflow), watsonx (Agent Node) |
| 1.1.19 | Unified Skill Selector | Agent Development | Unified picker for Tools/KBs/Workflows/sub-Agents. **Trigger**: same as 1.1.18. | Copilot Studio, Agentforce |
| 1.1.20 | Nested Graph Visualization | Agent Development | Expand/collapse sub-graphs in canvas. **Trigger**: same as 1.1.18. | ADK (subgraph), LangGraph (subgraph) |
| 6.21 | Decision Lineage | Observability & Operations | Record decision lineage: who made what decision based on what data version at what time; feedback learning + compliance auditing. Planned enhancements EF3 (Data Lineage Pipeline: full RAG provenance) + OE4 (Data-to-Decision Full-Chain Traceability). **Trigger**: requires Ontology foundation first (data + function + app version binding per trace, Palantir standard) — effort underestimated initially. | Palantir Decision Lineage, Palantir AIP, AgentArts (fka Versatile), Salesforce Data 360 |

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

## Dropped Features (2026-08-14 competitor-analysis re-scope)

> Removed from the catalog — the industry has moved beyond these; Hecate self-building is negative ROI. Full rationale and evidence: `docs/research/2026-08-competitor-analysis.md` §Feature decisions.

| # | Feature | Original Priority | Reason | Evidence |
|---|---------|------------------|--------|----------|
| 7.6a | Prompt Auto-Optimization | P3 | Specialized frameworks have standardized this; maintaining a niche tool against specialized competition is negative ROI | IBM watsonx AgentOps (Jul 2026, GEPA optimization, chat-based eval→optimize→deploy), DSPy (Stanford, mature) |
| 7.6b | Prompt Comparison | P3 | Evaluation platforms cover prompt comparison natively | LangSmith (100k+ MAU), Salesforce A/B Testing API (pilot, TDX 2026), Braintrust |
| 9.2a | Content Moderation | P3 | Model built-in safety layers + purpose-built free moderation APIs outperform platform-level moderation | GPT-5/Claude Opus 5 built-in safety, OpenAI Moderation API (free) |
| 6.17 | DSL Conversion Framework | P3 | MCP/A2A protocol standardization reduces DSL conversion value; industry converging on standard agent definitions | Salesforce Agent Script (open source, TDX 2026), MCP 97M downloads/month |
| 9.8 | Full-Chain Network Security | P3 | TLS/WAF/API Gateway/NetworkPolicy are infrastructure-layer concerns; belong in deployment guides, not platform features | All 18 surveyed platforms delegate to infrastructure (K8s NetworkPolicy, Istio, cloud WAF) |
