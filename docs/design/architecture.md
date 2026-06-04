# Hecate Top-Level Architecture Design

> **Version**: v0.2
> **Date**: 2026-05-16
> **Status**: Draft
> **Scope**: Top-level architecture design for the Hecate enterprise Agent platform, focusing on P1 scope (months 1-3, 19 core features), with P2-P4 noted as extension points
> **Research Basis**: 26 project surveys + 7 comprehensive reports + Huawei Finance Agent construction guide + RelayAgent architecture analysis

---

## Architecture Decision Records (ADR)

This section summarizes all confirmed architecture decisions. Each decision includes background, conclusion, and rationale.

### AD-1: Orchestration Pattern — Graph-first with Three-Layer Agent as Preset Template

- **Background**: Need to determine the basic paradigm for Agent orchestration — fixed layering vs. general-purpose graph vs. code-first
- **Conclusion**: Graph orchestration as primary, three-layer Agent (Guard→Plan→Sub-Agent) as preset workflow template
- **Rationale**: Three-layer Agent is a special case of fixed Graph topology, not a replacement. Progressive complexity (conversation→three-layer Agent template→canvas→code SDK), each level is backward compatible
- **Competitor Comparison**: Huawei Finance Agent hardcodes 6 orchestration patterns; RelayAgent hardcodes three-layer Agent; Hecate unifies with general-purpose Graph, three-layer Agent is just a template

### AD-2: System Layering — Five-Layer Architecture

- **Background**: Need to determine system layering approach, balancing modularity and complexity
- **Conclusion**: Five-layer architecture — Gateway→Orchestration→Execution Engine→Capability Services→Infrastructure
- **Rationale**: Decoupling the orchestration layer (what to do) from the execution engine layer (how to run) enables independent evolution and replacement
- **Competitor Comparison**: Dify uses four layers (Gateway→Orchestration→Services→Data); Huawei uses four layers (Gateway→Services→Engine→Infrastructure); Hecate adds an independent orchestration layer

### AD-3: Session State — Checkpoint Persistence + Memory Cache

- **Background**: Need to determine Session state management strategy — stateful vs. stateless vs. hybrid
- **Conclusion**: P1 implements Checkpoint persistence interface (PostgreSQL), allows memory cache for hot path acceleration, cache consistency optimized in P2
- **Rationale**: Checkpoint interface must exist from P1 (supports breakpoint recovery, time-travel debugging), memory cache is a performance optimization
- **Competitor Comparison**: RelayAgent is strictly stateless (Session V2 + event sourcing rebuild); LangGraph Checkpoint is optional; Hecate takes a middle-ground approach

### AD-4: Skill System — P1 Core / P2 Enhanced

- **Background**: The Skill system is one of Hecate's core differentiators; need to determine P1/P2 boundary
- **Conclusion**: P1 implements SKILL.md format + multi-source discovery (system/user/project) + on-demand loading; P2 adds knowledge graph auto-selection + Play advisory mode + remote sources + role overlay
- **Rationale**: P1 Skill core is sufficient to support three-layer Agent template's Sub-Agent dynamic loading; knowledge graph and other enhancements are not P1 blockers
- **Competitor Comparison**: RelayAgent has 4-Tier Skill + knowledge graph + Play mode; Claude Code uses SKILL.md format; Hecate P1 covers Claude Code's pattern, P2 covers RelayAgent's enhancements

### AD-5: Execution Engine Distribution — Progressive Worker Pool

- **Background**: The current feature list does not explicitly define a path for multi-process/distributed execution support; need to determine evolution direction
- **Conclusion**: Pregel scheduler remains single-process (lightweight), actual Node execution dispatched to Worker Pool. P1 in-process thread pool → P2 cross-process Worker → P3 optional Temporal backend
- **Rationale**: Channel/Checkpoint ownership stays in Scheduler (simple), Workers are stateless and scalable (elastic), evolution to P3 replaces scheduler with Temporal (compatible)
- **Key Constraints**: Workers only receive Channel read-only snapshots, never directly modify Channels; interrupts notify Scheduler via WorkerResult; Workers are stateless and can be rescheduled
- **Competitor Comparison**: LangGraph OSS is single-process, Cloud version is distributed (closed-source); Dify is single-process + Celery async tasks; Hecate designs Worker interface from P1, with progressive scaling

### AD-6: Tiered Memory System — Four-Level Memory with Progressive Implementation

- **Background**: Research report `03-memory-system.md` has designed a complete four-level memory (L1-L4) + Huawei three-stage process (Build→Evolve→Retrieve) + Consolidation Agent, but implementing all in P1 is too heavy. BGE Embedding supplementary review confirms P1 default selection as BGE-M3
- **Conclusion**: Four-level memory implemented progressively by priority — P1 does L2 simplified + L4 (i.e., RAG), P2 does L1 + full L2 + L3, P3 does Consolidation Agent + entity graph

| Level | P1 | P2 | P3 |
|-------|----|----|-----|
| **L1 Working Memory** | ❌ Use system_prompt concatenation instead | ✅ Full MemoryBlock (named blocks, token budget, Agent-editable) | Read-only blocks, concurrency control |
| **L2 Conversation Memory** | ✅ Conversation history + basic truncation (oldest messages truncated when too long) | ✅ Full compression pipeline (snip→microcompact→autocompact) | 413 emergency compression |
| **L3 User Memory** | ❌ Not implemented | ✅ Mem0-style extraction + pgvector + multi-signal fusion ranking | Consolidation Agent + entity graph |
| **L4 Knowledge Memory** | ✅ Equivalent to RAG pipeline, not separately listed as "memory" | Enhanced: hybrid retrieval + reranking | Memory-RAG joint retrieval |

- **L4 RAG Embedding Selection (BGE-M3 supplementary review conclusion)**: P1 default is **BGE-M3** (569M params, 1024 dimensions, 8192 token length, MIT license), core advantages:
  - Dense + Sparse + ColBERT triple hybrid retrieval, naturally matching Qdrant dense + sparse dual vectors
  - 100+ language coverage, one model solves multilingual knowledge bases (Chinese, English, Japanese, Korean, etc.)
  - LlamaIndex native support + Qdrant hybrid index configuration
  - FP16 deployment uses only ~1.5 GB VRAM, CPU also works in development environments
- **P1 RAG Pipeline**: Docling parsing → text chunking (512-1024 tokens) → BGE-M3 encode (dense+sparse) → Qdrant hybrid index → Query encode → Hybrid Search → Top-K → LLM
- **P2 Enhancement**: bge-reranker-v2-m3 reranking + bge-code-v1 code knowledge base
- **Rationale**: P1 goal is "run a complete Agent application end-to-end"; multi-turn conversations need L2 (at least simplified), RAG is already in the feature list (i.e., L4). L1 working memory blocks and L3 cross-session memory are nice-to-have but not P1 blockers
- **Competitor Comparison**: Letta implements all four levels at once (steep learning curve); Mem0 only does L3 (single point); Claude Code only does L2 compression (no persistent memory); Hecate is progressive, each phase delivers usable value

### AD-7: Multi-Agent Orchestration — All Patterns Unified as Graph Templates, Provided Progressively

- **Background**: The feature list includes 10 multi-Agent orchestration patterns (hierarchical/handoff/pipeline/broadcast/peer-selection/expert-panel/central-controller, etc.), need to determine how to unify them within the Graph framework and define P1/P2/P3 boundaries
- **Core Insight**: All orchestration patterns can be expressed as Graphs — hierarchical = agent node nesting; handoff = Command(goto); pipeline = linear chain; broadcast = fan-out/fan-in; peer-selection = LLM routing loop. Therefore no pattern needs to be hardcoded; all are unified as Graph templates
- **Conclusion**: All patterns are pre-compiled Graph templates, progressively added to the template library by phase

| Phase | Provided Patterns | Description |
|-------|-------------------|-------------|
| **P1** | Hierarchical delegation | Already covered by three-layer Agent template (Guard→Plan→Sub-Agent), no additional work needed |
| **P2** | Handoff + multi-Agent visual orchestration | Handoff is the most common scenario (customer service→expert, general→vertical); canvas is P2 core deliverable |
| **P3** | Pipeline + Broadcast + Peer-selection + Expert-panel + Central-controller + Conflict resolution + Inter-Agent communication | Gradually add preset templates, each template is a pre-compiled Graph |

- **Implementation**: `agent` type node is the unified primitive (references another Agent, maps state parent→child), all patterns are built by combining agent nodes + condition nodes + Commands to construct different Graph topologies
- **Competitor Comparison**: Coze hardcodes Multi-Agent patterns; AutoGen provides GroupChat abstraction but no visual orchestration; CrewAI supports Sequential/Hierarchical but not free topology; Hecate unifies all patterns with general-purpose Graph, with canvas visualization

### AD-8: Security & Authorization — Cross-Cutting Concern, Implemented via Plugin Extension Points, Progressive Enhancement

- **Background**: Security is a cross-cutting concern (spanning Gateway→Orchestration→Engine→Services layers), the feature list includes four-level risk authorization, approval scopes, security guardrails, audit logging, and sandbox isolation. After the LLM Guard + OWASP LLM Top 10 supplementary review, the security layered architecture and risk coverage are more clearly defined
- **Conclusion**: Security policies are implemented through the Plugin system's Decision and Observe extension points, not hardcoded in the engine. Progressive enhancement:

| Phase | Security Capability | Description |
|-------|-------------------|-------------|
| **P1** | Basic content filtering + API Key authentication + LLM Guard four Scanners | Minimum security baseline: injection/leakage prevention + simple authentication |
| **P2** | Four-level risk authorization + Once/Session scope + sandbox isolation | Tool call authorization confirmation; code execution container isolation |
| **P3** | Complete guardrails (input/output/retrieval/execution four layers) + Project/Global scope + audit logging + SSO/LDAP | Enterprise-grade security compliance |

- **Security Layered Architecture (LLM Guard + NeMo Guardrails Complementary)**:

```
User Request
  │
  ▼
┌─────────────────────────┐
│  NeMo Guardrails (outer) │  ← Conversation flow, topic constraints, behavioral boundaries
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│  LLM Guard (inner)       │  ← Content-level security scanning
│  P1 Input Scanners:      │
│  - PromptInjection       │
│  - Anonymize (PII)       │
│  - Secrets               │
│  - Toxicity              │
└────────────┬────────────┘
             │
             ▼
        LLM Inference
             │
             ▼
┌─────────────────────────┐
│  LLM Guard (inner)       │
│  P1 Output Scanners:     │
│  - Sensitive (PII)       │
│  - Toxicity              │
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│  NeMo Guardrails (outer) │  ← Output compliance check
└────────────┬────────────┘
             │
             ▼
         User Response
```

- **OWASP LLM Top 10 (2025) Risk Mapping**: P1 should focus on LLM01 (Prompt Injection), LLM02 (Sensitive Information Disclosure), LLM05 (Improper Output Handling), LLM07 (System Prompt Leakage), LLM10 (Unbounded Consumption); P2 adds LLM06 (Excessive Agency — core Agent risk) and LLM08 (Vector/Embedding Weaknesses); P3 covers all 10 items
- **Architecture Reservations**: Tool/Agent entities already have `risk_level` (LOW/MEDIUM/HIGH/CRITICAL) and `approval_scope` (once/session/project/global) fields, present from P1 but enforced only from P2. Among the five Plugin extension point types (Transform/Decision/Observe/Lifecycle/Registration), Decision is used for authorization decisions, Observe for audit logging
- **P1 Security Baseline Details**:
  - LLM Guard PromptInjection Scanner (DeBERTa-v3 classification model) — corresponds to OWASP LLM01
  - LLM Guard Anonymize + Deanonymize (Presidio + BERT NER) — corresponds to OWASP LLM02
  - LLM Guard Secrets Scanner (detect-secrets) — corresponds to OWASP LLM02
  - LLM Guard Toxicity Scanner (input + output) — basic content safety
  - NeMo Guardrails topic control — corresponds to OWASP LLM06/LLM07
  - API Key authentication + Rate Limiting — corresponds to OWASP LLM10
- **Competitor Comparison**: RelayAgent enforces four-level risk authorization from day one (personal Agent scenario); OpenClaw has session lanes for conflict isolation; Hecate targets enterprise, P1 minimum baseline + LLM Guard inner scanning, P2/P3 progressively stricter

### AD-9: API Design — OpenAI-Compatible + Hecate Management API Dual Track

- **Background**: Hecate needs to support both OpenAI-compatible interfaces (seamless integration with existing tools) and its own management API (Agent/Workflow/Session CRUD). Need to determine path design and P1 boundaries
- **Conclusion**: OpenAI-compatible interface keeps `/v1/` path without extensions, Hecate-specific capabilities use `/api/` RESTful path, dual tracks in parallel

| API Category | Path Prefix | P1 | P2 | P3 |
|-------------|------------|-----|-----|-----|
| **OpenAI Compatible** | `/v1/chat/completions`, `/v1/models` | ✅ Core conversation + model list | Tool call streaming | Multi-modal |
| **Agent Management** | `/api/agents` CRUD | ✅ Create/Read/Update/Delete | Version management | Canary deployment |
| **Workflow Management** | `/api/workflows` CRUD | ❌ P1 uses three-layer template | ✅ Full CRUD + versioning | Import/Export |
| **Session Management** | `/api/sessions` | ✅ Create/List/Resume | History query | Admin dashboard |
| **Knowledge Base** | `/api/knowledge-bases` | ✅ Create/Upload/Search | Document parsing status | Auto sync |
| **Tool Management** | `/api/tools` | ✅ List (built-in + MCP discovery) | Custom tool CRUD | Tool marketplace |
| **Skill Management** | `/api/skills` | ✅ List + Load | CRUD + remote sources | Knowledge graph |
| **Prompt Management** | `/api/prompts` | ❌ P1 hardcoded in Agent config | ✅ Version + tags | A/B testing |
| **Authentication** | `Authorization: Bearer <api_key>` | ✅ API Key | OAuth 2.0 | SSO/LDAP |

- **Design Conventions**: `/v1/` path strictly compatible with OpenAI spec (no extended field names), `/api/` follows RESTful + JSON + unified error format (`{error: {code, message, details}}`), authentication unified with Bearer token
- **Competitor Comparison**: Dify has its own API + OpenAI compatibility (but compatibility layer is incomplete); Coze has purely proprietary API; LangGraph has no platform API (SDK only); Hecate dual-track design, compatibility layer is highest priority

### AD-10: Frontend Canvas — React Flow + JSON DSL Bidirectional Sync

- **Background**: One of P2's core deliverables is the visual canvas; need to determine technology selection and architecture approach. P1 does not need a canvas, only a conversation UI
- **Conclusion**: React Flow as the canvas engine, custom node components correspond to Node types, JSON DSL as single source of truth, canvas is a visual editor for the DSL

| Decision Point | Choice | Rationale |
|---------------|--------|-----------|
| **Canvas Library** | React Flow | Open-source MIT, active community, flexible custom nodes/edges, Mini Map + Controls out of the box |
| **Node Rendering** | One custom React component per Node type | `llm`, `code`, `condition`, `tool`, `agent`, `subgraph` each has different UI |
| **Edge Types** | Conditional edges labeled (true/false), default style for normal edges | Corresponds to Graph DSL edge definition |
| **Bidirectional Sync** | Canvas operation → JSON DSL → compiler; JSON DSL change → canvas update | Single source of truth is JSON DSL |
| **Frontend Framework** | React 19 + TypeScript + Vite | Consistent with React Flow ecosystem |
| **P1 Frontend** | Pure conversation UI (Chat interface), no canvas | P1 does not need canvas |
| **P2 Frontend** | Conversation UI + Agent configurator + canvas + knowledge base management | Complete developer interface |

- **Competitor Comparison**: Coze has proprietary canvas tied to their own components; Dify uses React Flow but hardcodes node types; Langflow uses React Flow but only Python execution; Hecate uses React Flow + custom nodes + JSON DSL bidirectional sync, Graph-first architecture

---

## Chapter 1: Product Positioning & Design Principles

### 1.1 One-Sentence Definition

Hecate is an **open-source, self-hosted, model-agnostic, MCP-first** enterprise Agent platform that enables enterprises to build, orchestrate, and run AI Agent applications on their own infrastructure, rejecting vendor lock-in.

### 1.2 What Hecate Is Not

- Not an Agent framework (like LangGraph/AutoGen/CrewAI) — frameworks are libraries for developers; Hecate is a platform for enterprises
- Not a SaaS service (like Coze/Dashboard/Bailian) — Hecate is self-hosted, data sovereignty stays with the user
- Not an Agent application (like RelayAgent/Claude Code) — Hecate is a platform for users to build Agent applications

### 1.3 Core Differentiators

| Differentiation Dimension | vs Commercial Platforms (Bailian/Qianfan/Coze) | vs Open-Source Frameworks (LangGraph/AutoGen/Dify) |
|--------------------------|------------------------------------------------|---------------------------------------------------|
| **Self-hosted First** | All are SaaS, no air-gapped deployment support | LangGraph requires LangSmith; Hecate is fully self-contained |
| **MCP-first Architecture** | MCP is an add-on node type; Hecate uses MCP as the primary integration protocol | No framework natively supports MCP Client+Server |
| **Model-Agnostic** | Each vendor locks in their own models (Qwen/ERNIE/Doubao) | No built-in multi-Provider routing; Hecate uses LiteLLM |
| **Visual Canvas + Code** | Has canvas but poor extensibility | Langflow has canvas but only Python execution; Hecate is multi-language |
| **Enterprise Memory** | Basic or no memory system | Mem0/Letta are standalone components; Hecate integrates both patterns |
| **Open-Source Core** | None are open-source | LangGraph is open-source but depends on LangSmith; Hecate is fully open-source |

### 1.4 Six Design Principles

#### Principle 1: Open Over Closed

- Model-agnostic: supports 100+ LLM Providers via LiteLLM, not tied to any model vendor
- Open protocols: MCP (tool interoperability) + A2A (inter-Agent interoperability) as first-class citizens
- Standards-compatible: API interface compatible with OpenAI format, Skill format compatible with Claude Code
- No vendor lock-in: this is Hecate's core brand promise

#### Principle 2: Composable Over Monolithic

- MCP-first: all external capabilities integrated via MCP protocol, not hardcoded integrations
- Module decoupling: execution engine, memory service, RAG pipeline, tool system are independently replaceable
- Preset templates: three-layer Agent (Guard→Plan→Sub-Agent) is a preset, not a constraint; users can customize any orchestration
- Plugin extension: Plugin system provides Transform/Decision/Observe/Lifecycle/Registration five extension point types

#### Principle 3: Observable Over Black Box

- Full-chain tracing: every request from gateway to execution to response, complete Trace→Span→Generation hierarchy
- Checkpoint traceability: execution state persistence, supports "time-travel" debugging
- Evaluation-driven: every reasoning step of the Agent must be recordable, displayable, and evaluable
- Cost transparency: real-time Token and cost statistics per user/Agent/session

#### Principle 4: Security Built-in, Not Bolted-on

- Four-level risk authorization: LOW (auto-approve) → MEDIUM → HIGH → CRITICAL (cannot be auto-approved), supports Once/Session/Project/Global four scopes
- Security guardrails: input/output/retrieval/execution four-layer security checks
- Audit logging: full operation audit, meeting compliance requirements
- Sandbox isolation: code execution runs in hardened containers, with network/resource/filesystem isolation

#### Principle 5: Progressive Complexity

Users do not need to understand all concepts from the start; complexity increases naturally with usage depth:

```
Level 0: Conversation mode — chat directly with Agent (similar to ChatGPT)
Level 1: Three-layer Agent template — one-click enable Guard→Plan→Sub-Agent, zero configuration
Level 2: Visual canvas — drag-and-drop orchestration of custom workflows, deterministic + non-deterministic hybrid
Level 3: Code SDK — full programming control, advanced users
```

Each Level is backward compatible — Level 0 conversations can seamlessly upgrade to Level 1 three-layer Agent, Level 1 templates can be edited in Level 2 canvas.

#### Principle 6: Developer Experience First

- Low-code + high-code dual track: canvas and SDK are two interfaces to the same system, not two separate products
- Hot reload: Agent configuration and workflow modifications take effect in real-time
- Consistent abstraction: whether canvas operation or SDK call, the underlying execution engine is identical
- Comprehensive CLI: command-line creation, testing, and deployment of Agents

---

## Chapter 2: System Layered Architecture

### 2.1 Five-Layer Overview

```
┌─────────────────────────────────────────────────────────┐
│                    Gateway Layer                          │
│  API Gateway · WebSocket/SSE · Web Widget · Multi-channel│
│  Authentication · Rate Limiting · OpenAI-compatible API   │
├─────────────────────────────────────────────────────────┤
│                    Orchestration Layer                    │
│  Graph DSL Compiler · Workflow Management · Multi-Agent   │
│  Orchestration Strategies · Preset Templates              │
│  (Conversation/Three-layer Agent/Fixed Workflow)          │
│  · Human-in-the-Loop                                     │
├─────────────────────────────────────────────────────────┤
│                  Execution Engine Layer                   │
│  Pregel Runtime · Channel State · Checkpoint Persistence  │
│  interrupt/Command · Subgraph Composition                 │
│  · Strategy System · Streaming Output                     │
├─────────────────────────────────────────────────────────┤
│                  Capability Services Layer                │
│  Model Routing · RAG Pipeline · Memory Service            │
│  · Tool System (MCP) · Skill Management                  │
│  · Security Guardrails · Context Management               │
├─────────────────────────────────────────────────────────┤
│                  Infrastructure Layer                     │
│  PostgreSQL · Qdrant · MinIO · LangFuse                   │
│  · Container Orchestration · Auth (OAuth/OIDC/LDAP)      │
│  · Logging · Monitoring                                   │
└─────────────────────────────────────────────────────────┘
```

### 2.2 Layer Responsibilities & Interfaces

#### Gateway Layer

| Module | Responsibility | P1 Scope |
|--------|---------------|----------|
| API Gateway | REST API routing, authentication, rate limiting | ✅ OpenAI-compatible API + basic API Key authentication |
| WebSocket/SSE | Streaming response push | ✅ SSE streaming output |
| Web Widget | Embedded chat component | P2 |
| Multi-channel Adaptation | Feishu/WeCom/DingTalk/Slack etc. | P2 |
| Authentication | OAuth 2.0 / OIDC / LDAP | P1: API Key; P3: SSO/LDAP |

**Gateway to lower layer interface**: All requests are uniformly wrapped as `ExecutionRequest` and passed to the orchestration layer.

```
ExecutionRequest {
    agent_id: UUID
    messages: List[Message]
    stream: bool
    config: ExecutionConfig     # Model, temperature, tool list, etc.
    context: RequestContext     # User info, session ID, permissions, etc.
}
```

#### Orchestration Layer

| Module | Responsibility | P1 Scope |
|--------|---------------|----------|
| Graph DSL Compiler | JSON/YAML → Compiled Graph | ✅ Basic DAG compilation |
| Workflow Management | Workflow CRUD, version management | P2 |
| Orchestration Strategies | Deterministic routing, LLM routing, conditional branching | ✅ Route dispatch |
| Preset Templates | Conversation mode, three-layer Agent, fixed workflow | ✅ Conversation mode + three-layer Agent |
| HITL | Human approval, pause/resume | ✅ interrupt mechanism |
| Multi-Agent Orchestration | 10 patterns unified as Graph templates (AD-7) | P1: Hierarchical (three-layer Agent template); P2: Handoff + canvas orchestration; P3: Pipeline/Broadcast/Peer-selection/Expert-panel, etc. |

**Orchestration to lower layer interface**: Compiled `CompiledGraph` is passed to the execution engine layer for running.

```
CompiledGraph {
    nodes: Map[NodeId, NodeSpec]
    edges: List[EdgeSpec]
    channels: Map[ChannelName, ChannelSpec]
    entry_point: NodeId
    state_schema: TypeDict
}
```

**Key Design Decision: Three-Layer Agent as Preset Template**

The three-layer Agent is not a hardcoded path in the orchestration layer, but a predefined Graph template:

```
Guard→Plan→Sub-Agent Template = CompiledGraph {
    nodes: {
        "guard": GuardNode,          # Security check, long task planning
        "plan": PlanNode,            # Task decomposition, Skill selection
        "sub_agent": DynamicSubAgent # Dynamically generated based on Skill
    },
    edges: [
        START → "guard",
        "guard" → "plan",
        "plan" → condition("sub_agent" | END),
        "sub_agent" → "plan"         # Loop until complete
    ]
}
```

When the user selects "three-layer Agent mode", the orchestration layer automatically instantiates this template. Users can view and edit this Graph in the canvas.

#### Execution Engine Layer

| Module | Responsibility | P1 Scope |
|--------|---------------|----------|
| Pregel Runtime | BSP superstep loop: read Channel → execute Node → write Channel | ✅ |
| Channel System | State management: LastValue, Topic, PersistentTopic, Accumulator | ✅ |
| Checkpoint | State persistence to PostgreSQL, supports breakpoint recovery | ✅ |
| interrupt/Command | Human-in-the-Loop: pause and wait, resume and continue | ✅ |
| Subgraph Composition | Nested Graph, state mapping (parent→child) | P2 |
| Strategy System | Retry, Timeout, Cache, Fallback | ✅ Retry + Timeout + Fallback |
| Streaming Output | 7 stream modes | ✅ 4 (values, updates, messages, debug) |

**Execution Engine to lower layer interface**: Calls capability services layer via Port interface.

```
EnginePorts {
    llm_invoke(messages, config) → Stream[Token]        # Model invocation
    tool_execute(name, args, context) → Result           # Tool execution
    memory_query(query, scope) → List[Memory]            # L3 user memory retrieval (P2)
    memory_store(key, value, scope) → void               # L3 user memory storage (P2)
    conversation_load(session_id) → ConversationHistory  # L2 conversation memory loading (P1)
    conversation_save(session_id, messages) → void       # L2 conversation memory saving (P1)
    knowledge_query(query, kb_ids) → List[Chunk]         # Knowledge base retrieval
    skill_load(name) → SkillContent                      # Skill loading
    checkpoint_save(state) → CheckpointId                 # State persistence
    checkpoint_load(id) → State                          # State recovery
}
```

**Key Design Decision: Checkpoint Persistence + Memory Cache**

- Checkpoint interface must be implemented from P1; all state changes are persisted via Checkpoint
- Memory cache allowed as hot path acceleration (cache updated after successful write)
- Database writes can be asynchronous (WAL first, background flush)
- Cache consistency solutions (TTL invalidation / write-through / event notification) optimized in P2

#### Capability Services Layer

| Module | Responsibility | P1 Scope |
|--------|---------------|----------|
| Model Routing | LiteLLM integration, 100+ Providers, degradation/Fallback | ✅ Multi-model access + model fallback |
| RAG Pipeline | Document parsing → chunking → Embedding → retrieval | ✅ Basic RAG (Docling + Qdrant) |
| Memory Service | Four-level memory (L1-L4) progressive implementation | P1: Simplified L2 (conversation history + truncation); P2: L1 working memory + full L2 compression + L3 user memory; P3: Consolidation Agent |
| Tool System | Built-in tools + custom tools + MCP Client | ✅ MCP client + built-in/custom tools |
| Skill Management | SKILL.md format + multi-source discovery + on-demand loading | ✅ Core Skill |
| Security Guardrails | Four-layer security (input/output/retrieval/execution) + four-level risk authorization (AD-8) | P1: Basic content filtering; P2: Four-level authorization + sandbox; P3: Complete guardrails + audit |
| Context Management | Write/Select/Compress/Isolate | P2 complete context management; P1 basic compression |

#### Infrastructure Layer

| Component | Purpose | P1 Scope |
|-----------|---------|----------|
| PostgreSQL | Relational data + Checkpoint + JSONB metadata | ✅ |
| Qdrant | Vector retrieval (Embedding + ANN) | ✅ |
| MinIO | File/document/artifact storage | ✅ |
| LangFuse | Observability: tracing, cost, Prompt management | ✅ Conversation logging + basic tracing |
| Docker Compose | Development/single-machine deployment | ✅ |
| Authentication | API Key authentication | ✅ API Key; P3: SSO/LDAP |

### 2.3 Key Differences from Reference Architectures

| Dimension | Huawei Finance Agent | RelayAgent | Hecate |
|-----------|---------------------|------------|--------|
| **Positioning** | Enterprise internal construction guide | Personal Agent application | Open-source Agent platform |
| **Orchestration** | Direct LangGraph usage | Hardcoded three-layer Agent | General-purpose Graph + three-layer Agent preset template |
| **Execution Engine** | LangGraph Runtime | AgentScope Runtime | Self-built engine (borrowing LangGraph design patterns) |
| **Frontend** | AUI (Huawei internal) | Vue 3 + tinyVue | React Flow + React 19 |
| **Deployment** | Huawei Cloud internal | Local/single-machine | Docker Compose → K8s → air-gapped environments |
| **Models** | MaaS + ModelArts | Single user configuration | LiteLLM 100+ Providers |
| **Stateless** | Not emphasized | Strictly stateless (Session V2) | Checkpoint persistence + memory cache |
| **Multi-tenancy** | Not covered | Not covered | P3 multi-tenancy + RBAC |

---

## Chapter 3: Core Concept Model

### 3.1 Entity Relationship Overview

```
Organization ─┬── Workspace ─┬── Agent ─┬── has Tools
              │              │          ├── has Skills
              │              │          ├── has MemoryBlocks
              │              │          ├── has KnowledgeBases
              │              │          └── runs Workflow
              │              │
              │              ├── Workflow ─┬── has Nodes
              │              │             └── has Edges
              │              │
              │              ├── KnowledgeBase ─── Document ─── Chunk
              │              │
              │              ├── Prompt (versioned)
              │              │
              │              └── Plugin
              │
              └── User ─── has Role
```

### 3.2 Core Entity Definitions

#### Agent

Agent is Hecate's core concept — an autonomous execution unit with persona, model, tools, knowledge, and memory.

```python
class Agent:
    id: UUID
    name: str
    workspace_id: UUID

    # Identity
    persona: str                    # System prompt / persona description
    model_config: ModelConfig       # Primary model + fallback model + parameters

    # Capabilities
    tools: List[ToolRef]            # Associated tools (built-in + custom + MCP)
    skills: List[SkillRef]          # Associated Skills
    knowledge_bases: List[UUID]     # Associated knowledge bases

    # Memory
    memory_blocks: List[MemoryBlock]  # L1 working memory (P2)
    memory_config: MemoryConfig       # Memory strategy (P2, including three-stage process config)

    # Execution
    workflow_id: Optional[UUID]       # Bound workflow (None = pure conversation mode)
    mode: AgentMode                   # chat | three_layer | workflow

    # Security
    risk_level: RiskLevel             # Default risk level
    approval_rules: List[ApprovalRule]

    # Metadata
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime]
```

#### Workflow

Workflow is an executable directed graph that defines the Agent's execution flow.

```python
class Workflow:
    id: UUID
    name: str
    workspace_id: UUID
    version: int

    # Graph definition
    nodes: List[Node]
    edges: List[Edge]
    entry_node: NodeId

    # State definition
    state_schema: dict               # Channel definition (field name → Channel type)
    state_defaults: dict              # Channel default values

    # Metadata
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime]

class Node:
    id: NodeId
    type: NodeType                    # llm | code | condition | tool | agent | subgraph | input | output
    config: dict                      # Node configuration (model, tools, code, etc.)
    position: Optional[Position]      # Canvas position (for frontend)

class Edge:
    id: EdgeId
    source: NodeId
    target: NodeId
    source_handle: Optional[str]      # Output port
    target_handle: Optional[str]      # Input port
    condition: Optional[str]          # Condition expression (conditional edge)
```

#### Node Types

| Type | Description | Channel Read/Write |
|------|-------------|-------------------|
| `llm` | LLM inference node | Reads messages/tools → writes response |
| `code` | Code execution node (sandbox) | Reads input → writes output |
| `condition` | Conditional branch node | Reads state → routes to corresponding branch |
| `tool` | Tool invocation node | Reads tool_name/args → writes result |
| `agent` | Sub-Agent node (references another Agent) | Maps parent state → child state → returns result |
| `subgraph` | Subgraph node (nested Workflow) | Maps outer state → inner state → returns result |
| `input` | Workflow input node | Receives external input |
| `output` | Workflow output node | Outputs final result |

#### Tool

```python
class Tool:
    id: UUID
    name: str
    description: str                  # Natural language description (Agent-understandable)
    source: ToolSource                # builtin | custom | mcp

    # Schema
    parameters: dict                  # JSON Schema (input)
    returns: dict                     # JSON Schema (output)

    # Security
    risk_level: RiskLevel             # LOW | MEDIUM | HIGH | CRITICAL
    approval_required: bool
    approval_scope: ApprovalScope     # once | session | project | global

    # MCP (when source=mcp)
    mcp_server: Optional[str]
    mcp_tool_name: Optional[str]

class ToolSource(str, Enum):
    builtin = "builtin"               # Built-in tools (code execution, file operations, etc.)
    custom = "custom"                  # User-defined (API Schema)
    mcp = "mcp"                        # Provided by MCP server
```

#### Skill

```python
class Skill:
    id: UUID
    name: str                         # Lowercase letters + hyphens, e.g. "developer"
    description: str                  # One-sentence description
    source: SkillSource               # system | user | project

    # Content
    instructions: str                 # SKILL.md body (Agent instructions)
    allowed_tools: List[str]          # List of allowed tools
    metadata: dict                    # Metadata (~100 tokens)

    # Resources
    scripts: List[str]                # Executable script paths
    references: List[str]             # Reference document paths
    assets: List[str]                 # Asset file paths

    # Load control
    max_tokens: int                   # Maximum Token limit
    auto_load: bool                   # Whether to auto-load

class SkillSource(str, Enum):
    project = "project"               # Project-level: {project}/.skills/
    user = "user"                     # User-level: ~/.hecate/skills/
    system = "system"                 # System-level: platform built-in
```

#### KnowledgeBase / Document / Chunk

```python
class KnowledgeBase:
    id: UUID
    name: str
    workspace_id: UUID
    embedding_model: str              # Embedding model used
    chunk_strategy: ChunkStrategy     # auto | fixed | semantic
    chunk_size: int
    chunk_overlap: int

class Document:
    id: UUID
    knowledge_base_id: UUID
    filename: str
    file_type: str                    # pdf | docx | md | html | ...
    parsing_status: ParsingStatus     # pending | parsing | completed | failed
    chunk_count: int

class Chunk:
    id: UUID
    document_id: UUID
    content: str                      # Chunked text content
    metadata: dict                    # Metadata (page number, position, title, etc.)
    embedding: List[float]            # Vector
```

#### Memory — Four-Level Memory System

Four-level memory implemented progressively (AD-6). L2 conversation memory is required for P1 (multi-turn conversation necessity), L1/L3 introduced in P2, L4 is equivalent to RAG pipeline.

```python
class MemoryBlock:
    """L1 Working Memory — named area in context window (P2)"""
    id: UUID
    agent_id: UUID
    label: str                        # e.g. "persona", "user_profile", "domain_context"
    content: str
    position: int                     # Position in context
    limit: int                        # Maximum Token count

class ConversationHistory:
    """L2 Conversation Memory — conversation history + auto-compression"""
    # P1: Simple message list + truncation when too long
    # P2: Full compression pipeline (snip→microcompact→autocompact)
    messages: List[Message]
    summary: Optional[str]            # Compression summary (P2)
    token_count: int                  # Current Token count

class Memory:
    """L3 User/Agent Memory — cross-session persistent facts (P2)"""
    id: UUID
    content: str                      # Extracted facts/preferences/knowledge
    scope: MemoryScope                # user + agent + session
    memory_type: MemoryType           # semantic | procedural | episodic
    importance: float                 # Importance score
    access_count: int                 # Access count
    embedding: List[float]            # Vector
    created_at: datetime
    updated_at: datetime

# L4 Knowledge Memory = RAG pipeline (KnowledgeBase + Document + Chunk), defined above

class MemoryScope:
    user_id: str
    agent_id: UUID
    session_id: Optional[UUID]

class MemoryType(str, Enum):
    semantic = "semantic"             # Factual knowledge
    procedural = "procedural"         # Method/procedure
    episodic = "episodic"             # Event/experience
```

#### Conversation / Message / Session

```python
class Conversation:
    id: UUID
    agent_id: UUID
    user_id: str
    created_at: datetime

class Message:
    id: UUID
    conversation_id: UUID
    role: str                         # system | user | assistant | tool
    content: str
    tool_calls: Optional[List[dict]]  # Tool call list
    tool_call_id: Optional[str]       # Tool call result association ID
    metadata: dict                    # Token usage, model, latency, etc.
    created_at: datetime

class Session:
    """A complete Agent execution context"""
    id: UUID
    conversation_id: UUID
    agent_id: UUID
    status: SessionStatus             # active | interrupted | completed | failed
    current_node: Optional[NodeId]    # Currently executing node
    checkpoint_id: Optional[UUID]     # Latest Checkpoint
    created_at: datetime
    updated_at: datetime
```

#### Prompt

```python
class Prompt:
    id: UUID
    name: str
    workspace_id: UUID
    template: str                     # Prompt template (supports variable interpolation)
    variables: List[str]              # Template variable list
    version: int
    labels: List[str]                 # production | staging | development
    created_at: datetime
```

#### ResourceVersion — Generic Resource Versioning (P2)

Versionable resources such as Agent, Workflow, Prompt, Skill share the same version management mechanism, unified in P2, extended in P3 with canary deployment and change approval.

```python
class ResourceVersion:
    resource_type: str                # "agent" | "workflow" | "prompt" | "skill"
    resource_id: UUID
    version: int                      # Monotonically increasing
    snapshot: dict                    # Complete configuration snapshot for this version (JSONB)
    change_summary: str               # Change description (auto-generated or user-provided)
    changed_by: UUID                  # Operator
    created_at: datetime
```

**Applicable Scope**:

| Resource Type | Versioned Content | P2 Capability | P3 Extension |
|--------------|-------------------|--------------|-------------|
| Agent | persona, model_config, tools, skills, kb_refs | View history, rollback | Draft/test/production environment isolation |
| Workflow | nodes, edges, state_schema | Listed in 1.1.9, diff, rollback | Version-level regression testing |
| Prompt | template, variables | Listed in 8.5, tag deployment | A/B testing |
| Skill | instructions, allowed_tools | View history, rollback | Remote source version sync |

#### Organization / User / Workspace

```python
class Organization:
    id: UUID
    name: str
    settings: dict                    # Organization-level configuration

class User:
    id: UUID
    org_id: UUID
    email: str
    name: str
    role: UserRole                    # admin | editor | viewer

class Workspace:
    id: UUID
    org_id: UUID
    name: str
    settings: dict                    # Workspace-level configuration
```

### 3.3 Storage Design

#### PostgreSQL Table Mapping

| Entity | Table Name | Key Fields |
|--------|-----------|------------|
| Agent | `agents` | id, workspace_id, name, persona, model_config(JSONB), mode |
| Workflow | `workflows` | id, workspace_id, name, version, nodes(JSONB), edges(JSONB) |
| Tool | `tools` | id, workspace_id, name, source, parameters(JSONB), risk_level |
| Skill | `skills` | id, name, source, description, instructions(TEXT), allowed_tools(JSONB) |
| KnowledgeBase | `knowledge_bases` | id, workspace_id, name, embedding_model, chunk_strategy |
| Document | `documents` | id, kb_id, filename, file_type, parsing_status |
| Chunk | `chunks` | id, document_id, content, metadata(JSONB), embedding(vector) |
| MemoryBlock | `memory_blocks` | id, agent_id, label, content, position, limit |
| Memory | `memories` | id, content, scope(JSONB), type, importance, embedding(vector) |
| Conversation | `conversations` | id, agent_id, user_id |
| Message | `messages` | id, conversation_id, role, content, tool_calls(JSONB), metadata(JSONB) |
| Session | `sessions` | id, conversation_id, agent_id, status, current_node, checkpoint_id |
| Checkpoint | `checkpoints` | id, session_id, node_id, channel_state(JSONB), metadata(JSONB) |
| Prompt | `prompts` | id, workspace_id, name, template, version, labels(JSONB) |
| Organization | `organizations` | id, name, settings(JSONB) |
| User | `users` | id, org_id, email, name, role |
| Workspace | `workspaces` | id, org_id, name, settings(JSONB) |
| ResourceVersion | `resource_versions` | resource_type, resource_id, version, snapshot(JSONB), change_summary, changed_by |

#### Design Conventions

- UUID primary keys, supporting distributed ID generation
- JSONB columns for flexible metadata and configuration storage
- Soft delete (`deleted_at` timestamp)
- Tenant isolation via `org_id` / `workspace_id` foreign keys (P3 enables Row-Level Security)
- Alembic manages schema evolution
- Checkpoint table partitioned by session_id (high-frequency writes, P2 optimization)

#### Mapping to LangGraph StateGraph

| Hecate Concept | LangGraph Equivalent | Hecate Extension |
|---------------|---------------------|-----------------|
| Workflow | `StateGraph` | JSON serialization, visual editing, version management |
| Node | `PregelNode` | Typed input/output, component metadata, 8 node types |
| Edge | `add_edge` / `add_conditional_edges` | Visual source/target handles, type validation |
| Channel | channels/ | Extended types: PersistentTopic, Accumulator |
| Checkpoint | `BaseCheckpointSaver` | PostgreSQL backend, multi-tenancy, audit |
| Execution | `Pregel` runtime | Optional distributed backend (Temporal), OTel tracing |
| Session | Thread ID | Full lifecycle management (active/interrupted/completed) |

---

## Chapter 4: Execution Engine Design

### 4.1 Design Positioning

The execution engine is Hecate's heart. It receives compiled Graphs, executes them following the Pregel model, manages state, handles interrupts, and outputs streaming results.

**Core Design Decision**: Self-built engine, borrowing five design patterns from LangGraph (Channel, Checkpoint, Pregel, interrupt/Command, subgraph), without depending on LangChain code.

**Code Volume Estimate**: ~5000 lines of core code (~2500 lines borrowed + ~2500 lines self-built).

### 4.2 Graph DSL

#### JSON Schema

```json
{
    "version": "1.0",
    "name": "my-workflow",
    "state": {
        "messages": { "type": "topic", "reduce": "append" },
        "current_plan": { "type": "last_value" },
        "iterations": { "type": "accumulator", "initial": 0 }
    },
    "nodes": {
        "guard": {
            "type": "llm",
            "config": {
                "model": "auto",
                "system_prompt": "You are a security guard...",
                "tools": ["content_filter", "risk_assessor"]
            }
        },
        "plan": {
            "type": "llm",
            "config": {
                "model": "auto",
                "system_prompt": "You are a task planner...",
                "tools": ["skill_selector", "task_decomposer"]
            }
        },
        "execute": {
            "type": "agent",
            "config": {
                "skill_ref": "{{ current_plan.selected_skill }}",
                "allowed_tools": "{{ current_plan.allowed_tools }}"
            }
        },
        "should_continue": {
            "type": "condition",
            "config": {
                "expression": "state.iterations < state.max_iterations AND state.current_plan.status != 'done'"
            }
        }
    },
    "edges": [
        { "source": "__start__", "target": "guard" },
        { "source": "guard", "target": "plan" },
        { "source": "plan", "target": "execute" },
        { "source": "execute", "target": "should_continue" },
        {
            "source": "should_continue",
            "targets": {
                "true": "plan",
                "false": "__end__"
            }
        }
    ]
}
```

#### Channel Types

| Type | Semantics | Write Behavior | Typical Use |
|------|-----------|---------------|-------------|
| `last_value` | Keeps the last value | New value overwrites old value | Current plan, current state |
| `topic` | Message stream | Append (supports reducer) | Conversation message list, tool call records |
| `persistent_topic` | Persistent message stream | Append + persistence | Audit log, immutable event stream |
| `accumulator` | Accumulator | Aggregate via specified function | Iteration counter, Token usage statistics |

### 4.3 Compiler

The compiler transforms Graph DSL (JSON) into a runtime-executable `CompiledGraph`:

```
JSON DSL
  │
  ├── 1. Schema Validation
  │     └── Validate node types, edge connections, Channel definitions
  │
  ├── 2. Dependency Analysis
  │     └── Build node dependency graph, detect circular dependencies (disallowed cycles)
  │
  ├── 3. Channel Binding
  │     └── Analyze each node's read/write Channels, verify type compatibility
  │
  ├── 4. Compilation Optimization
  │     └── Identify parallelizable nodes, merge consecutive pure-function nodes
  │
  └── 5. Output CompiledGraph
        ├── nodes: Map[NodeId, CompiledNode]
        ├── edges: Map[NodeId, List[CompiledEdge]]
        ├── channels: Map[ChannelName, ChannelInstance]
        └── entry_point: NodeId
```

### 4.4 Pregel Runtime + Worker Pool Distributed Execution

The execution engine adopts the Pregel/BSP (Bulk Synchronous Parallel) model, with node execution dispatched to the Worker Pool:

**Architecture Decision AD-5**: Pregel scheduler remains single-process (lightweight), actual Node execution (LLM calls, tool execution, code execution) dispatched to Worker Pool. Evolution path: P1 in-process thread pool → P2 cross-process Worker → P3 optional Temporal backend.

```
┌──────────────────────────────────────────────────────┐
│                  Pregel Scheduler (single-process)    │
│                                                       │
│  1. READ: Each node reads current Channel values      │
│  2. DISPATCH: Dispatch ready nodes to Worker Pool     │
│  3. AWAIT: Wait for all Workers to return results     │
│  4. WRITE: Each node writes new Channel values        │
│  5. CHECKPOINT: Persist current state                 │
│  6. ROUTE: Determine next step based on conditional   │
│     edges                                             │
│  7. CHECK: Are there still ready nodes?               │
│     ├── YES → Go back to Step 1                      │
│     └── NO → Execution complete                      │
│                                                       │
│  Can be paused at any time via interrupt()            │
└──────────────┬───────────────────────────────────────┘
               │ dispatch tasks
               ▼
┌──────────────────────────────────────────────────────┐
│                     Worker Pool                       │
│                                                       │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐              │
│  │Worker 1 │  │Worker 2 │  │Worker N │  ...          │
│  │(Thread) │  │(Thread) │  │(Thread) │              │
│  └────┬────┘  └────┬────┘  └────┬────┘              │
│       │            │            │                     │
│  P1: In-process thread pool (asyncio/concurrent.     │
│      futures)                                         │
│  P2: Cross-process Workers (multi-process /          │
│      multi-container)                                 │
│  P3: Optional Temporal / NATS distributed backend    │
└──────────────────────────────────────────────────────┘
```

#### Worker Interface

```python
class WorkerTask:
    task_id: UUID
    session_id: UUID
    node_id: NodeId
    node_type: NodeType              # llm | tool | code | ...
    node_config: dict
    channel_snapshot: dict            # Channel read-only snapshot needed by this node
    deadline: Optional[float]         # Timeout

class WorkerResult:
    task_id: UUID
    status: Literal["success", "error", "timeout", "interrupted"]
    output: dict                      # Channel write content
    metadata: dict                    # Token usage, elapsed time, etc.
    error: Optional[ErrorInfo]
```

#### Progressive Scaling Path

| Phase | Worker Implementation | Communication Method | Use Case |
|-------|----------------------|---------------------|----------|
| **P1** | In-process thread pool | Direct function call / asyncio | Single machine, low concurrency |
| **P2** | Cross-process Worker | IPC (multiprocessing / Redis Queue) | Multi-user concurrency, CPU-intensive tools |
| **P3** | Distributed Worker | Temporal Task Queue / NATS | High availability, horizontal scaling, multi-tenancy |

#### Design Constraints

- **Channel ownership in Scheduler**: Workers only receive Channel snapshots (read-only), never directly modify Channels. After Workers return results, the Scheduler uniformly writes to Channels.
- **Checkpoint controlled by Scheduler**: Workers are unaware of Checkpoints; the Scheduler uniformly persists after all Workers complete.
- **Interrupt triggered by Worker**: Worker calls `interrupt()` during execution → notifies Scheduler via `WorkerResult.status="interrupted"` → Scheduler pauses the loop.
- **Workers are stateless**: Workers do not save execution state; they can be rescheduled by the Scheduler after restart (based on Checkpoint recovery).

#### Execution Flow Example: Three-Layer Agent Template

```
Superstep 1:
  READ:   messages = [user_input]
  EXECUTE: guard node → security check + risk assessment
  WRITE:  guard_result = {safe: true, risk: LOW}
  CHECKPOINT: #1

Superstep 2:
  READ:   messages + guard_result
  EXECUTE: plan node → task decomposition + Skill selection
  WRITE:  current_plan = {skill: "developer", tasks: [...]}
  CHECKPOINT: #2

Superstep 3:
  READ:   messages + current_plan
  EXECUTE: execute node (developer Sub-Agent) → execute task
  WRITE:  messages.append(assistant_response), iterations += 1
  CHECKPOINT: #3

Superstep 4:
  READ:   iterations + current_plan
  EXECUTE: should_continue node → condition evaluation
  WRITE:  routing result = true/false
  → If true: go back to Superstep 2 (Plan re-evaluation)
  → If false: execution complete
```

### 4.5 Checkpoint Persistence

#### Design Principles

1. **Persist every step**: After each Pregel superstep completes, Checkpoint is written to PostgreSQL
2. **Memory cache**: Most recent Checkpoint cached in memory for fast recovery
3. **Asynchronous writes**: Database writes can be async (WAL first, background flush)
4. **Immutable**: Once written, Checkpoints cannot be modified, enabling time-travel

#### Checkpoint Structure

```python
class Checkpoint:
    id: UUID
    session_id: UUID
    superstep: int                    # Superstep number
    node_id: NodeId                   # Currently executing node
    channel_state: dict               # All Channel current values
    pending_writes: List[Write]       # Pending Channel updates
    metadata: dict                    # Execution metadata (elapsed time, Tokens, etc.)
    created_at: datetime
```

#### Recovery Flow

```
Session interrupted → User sends resume request
  │
  ├── 1. Load latest Checkpoint
  ├── 2. Rebuild Channel state
  ├── 3. Continue Pregel loop from interruption point
  └── 4. Optional: User modifies state then resumes ("time-travel")
```

### 4.6 interrupt / Command — Human-in-the-Loop

#### interrupt

Nodes can call `interrupt(value)` to pause execution, returning control to the user:

```python
# In an Agent node
def approval_node(state):
    if state["risk_level"] == "HIGH":
        user_decision = interrupt({
            "type": "approval",
            "operation": state["pending_operation"],
            "risk_level": "HIGH",
            "message": "This operation requires your approval"
        })
        if user_decision == "deny":
            return {"status": "cancelled"}
    return {"status": "approved"}
```

#### Command

Nodes can return `Command` objects to control execution flow:

```python
# Handoff to another Agent
Command(goto="other_agent", update={"context": "handoff data"})

# Resume interrupted execution
Command(resume=value, update={"user_decision": "approved"})
```

### 4.7 Subgraph Composition (P2)

Subgraphs allow nesting one Workflow inside another:

```
Outer Graph:
  ├── Node A
  ├── SubGraph B (Inner Graph)
  │     ├── Node B1
  │     ├── Node B2
  │     └── Node B3
  └── Node C

State Mapping:
  Outer Channel → Inner Channel (input mapping)
  Inner Channel → Outer Channel (output mapping)
```

**Namespace Isolation**: Channels inside subgraphs use namespace prefixes (e.g. `subgraph_b.messages`) to avoid conflicts with the outer layer.

### 4.8 Strategy System

| Strategy | Description | P1 |
|----------|-------------|-----|
| **Retry** | Exponential backoff retry, configurable max attempts, jitter, custom predicate | ✅ |
| **Timeout** | Node-level timeout, global timeout | ✅ |
| **Fallback** | Primary model failure → fallback model → default response | ✅ |
| **Cache** | Tool call result caching (same parameters return directly) | P2 |
| **Rate Limit** | Tool/model call rate limiting | P2 |
| **Circuit Breaker** | Tool/model circuit breaking | P3 |

### 4.9 Streaming Output

The execution engine supports 4 streaming modes (P1), later expanded to 7:

| Mode | Output Content | Use Case |
|------|---------------|----------|
| `values` | Complete state after each superstep | Debugging, state monitoring |
| `updates` | Incremental updates per superstep | Progress display |
| `messages` | LLM-generated Token stream | Frontend real-time Agent response display |
| `debug` | Internal execution details (tool calls, Channel changes) | Development debugging |
| `checkpoints` | Checkpoint events | P2: Time-travel UI |
| `tasks` | Sub-task status changes | P2: Multi-Agent task tracking |
| `custom` | User-defined streaming output | P2: Custom events |

### 4.10 Comparison with LangGraph

| Dimension | LangGraph | Hecate |
|-----------|-----------|--------|
| **Graph Definition** | Pure code (Python API) | JSON DSL + canvas + code (three entry points) |
| **Serialization** | No native serialization | JSON Schema native support |
| **State Model** | Channel (LastValue/Topic) | Extended Channel (+PersistentTopic/Accumulator) |
| **Checkpoint** | PostgreSQL/SQLite/memory | PostgreSQL primary, memory cache acceleration |
| **Streaming** | 7 modes | 4 (P1) → 7 (P2) |
| **Distribution** | Single-process (OSS) | Worker Pool progressive (P1 thread pool → P2 cross-process → P3 Temporal) |
| **Models** | Tied to LangChain BaseChatModel | LiteLLM model adapter layer, 100+ Providers |
| **Dependencies** | Strong dependency on langchain-core | Zero external framework dependencies |
| **DSL** | None | JSON/YAML → compiler → CompiledGraph |
| **Visualization** | None | React Flow canvas (P2) |

### 4.11 Engine Module Code Volume Estimate

| Module | Lines | Description |
|--------|-------|-------------|
| **Channel System** | ~500 | LastValue, Topic, PersistentTopic, Accumulator |
| **Checkpoint** | ~800 | PostgreSQL backend + memory cache + recovery logic |
| **Pregel Runtime** | ~400 | BSP superstep loop + scheduling |
| **Worker Pool** | ~600 | Worker interface + thread pool scheduling + task dispatch/result collection |
| **interrupt/Command** | ~300 | Pause/resume/control flow |
| **Subgraph Composition** | ~400 | State mapping + namespace isolation |
| **Graph DSL Serialization** | ~1000 | JSON Schema + compiler |
| **FSM Semantics** | ~300 | Explicit state machine + transitions + guards |
| **OTel Integration** | ~400 | traces/spans/metrics |
| **Model Adapter Layer** | ~300 | LiteLLM → Hecate interface |
| **Tool System** | ~500 | Tool definition/registration/execution/MCP Client |
| **Strategy System** | ~400 | Retry/Timeout/Fallback |
| **Total** | **~5900** | |

---

## References

| Reference | Location | Description |
|-----------|----------|-------------|
| Full Feature Set | `docs/features/feature-catalog.md` | 156 features, P1-P4 |
| Research Tracker | `docs/research/research-tracker.md` | 37/80 completed |
| Architecture Decision Overview | `docs/research/reports/00-architecture-decisions.md` | D1-D5 decisions |
| Execution Engine Decision | `docs/research/reports/01-execution-engine-decision.md` | D1 full discussion |
| Execution Engine Report | `docs/research/reports/01-execution-engine.md` | Engine comprehensive analysis |
| RAG Architecture | `docs/research/reports/02-rag-knowledge.md` | RAG layered design |
| Memory System | `docs/research/reports/03-memory-system.md` | Four-level memory |
| Infrastructure | `docs/research/reports/04-infrastructure.md` | Technology stack selection |
| Feature Parity | `docs/research/reports/05-feature-parity.md` | Differentiation analysis |
| Huawei Finance Agent Guide | `docs/refs/md/huawei-finance-agent-guide-summary.md` | Huawei four-layer architecture + orchestration patterns |
| RelayAgent Architecture | `docs/refs/md/relay-agent-summary.md` | Three-layer Agent + stateless + Skill + authorization |
