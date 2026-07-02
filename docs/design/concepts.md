# Hecate Core Concepts

> Entity definitions, relationships, and data model for the Hecate platform. For a system overview, see [Architecture](architecture.md). For execution engine details, see [Engine Design](engine-design.md).

---

## Entity Relationships

Hecate organizes entities in a hierarchical tenant model with capabilities attached to agents:

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
              │              ├── KnowledgeBase ─┬── Document ─── Chunk
              │              │                   └── GraphEntity ─── GraphRelation *(planned)*
              │              │
              │              ├── Prompt (versioned)
              │              │
              │              └── Plugin
              │
              └── User ─── has Role
```

An Organization owns multiple Workspaces. Each Workspace is an isolated environment containing Agents, Workflows, Knowledge Bases, Tools, Skills, and Prompts. Users belong to an Organization and are assigned roles (admin, editor, viewer) that govern their access across Workspaces.

---

## Agent

An Agent is Hecate's core execution unit — an autonomous entity with persona, model configuration, tools, knowledge, and memory. Agents operate in three modes:

- **Conversation mode** — Direct chat, similar to ChatGPT. No workflow; the agent responds to messages directly.
- **Three-layer mode** — Guard → Plan → Sub-Agent preset template. The guard performs security checks and risk assessment, the planner decomposes tasks and selects skills, and the sub-agent executes.
- **Workflow mode** — Custom directed graph defined via the visual canvas or JSON DSL.

Key attributes:

- **id** (UUID), **name** (str), **workspace_id** (UUID) — Identity and tenant isolation
- **persona** (str) — System prompt defining the agent's character and instructions
- **model_config** (ModelConfig) — Primary model, fallback model, and inference parameters (temperature, max tokens, etc.)
- **tools** (List[ToolRef]) — Associated tools from built-in, custom, or MCP sources
- **skills** (List[SkillRef]) — Associated skills for dynamic capability loading
- **knowledge_bases** (List[UUID]) — Associated knowledge bases for RAG retrieval
- **memory_blocks** (List[MemoryBlock]) — L1 working memory blocks (named regions in the context window)
- **mode** (AgentMode) — Execution mode: `chat` | `three_layer` | `workflow`
- **risk_level** (RiskLevel) — Security classification: `LOW` | `MEDIUM` | `HIGH` | `CRITICAL`
- **workflow_id** (Optional[UUID]) — Bound workflow (when mode = workflow)

The `model_config` field maps to a database column named `model_config` (not `model_config_db`) to avoid Pydantic's reserved `model_config` attribute collision.

---

## Workflow

A Workflow is an executable directed graph that defines an Agent's execution flow. Workflows are versioned — each modification creates a new version, and previous versions remain accessible for rollback.

A Workflow consists of **Nodes** and **Edges**:

- **Nodes** are typed execution units (LLM inference, code execution, condition evaluation, tool invocation, sub-agent reference, subgraph, input, output)
- **Edges** connect nodes and may carry conditional expressions for branching

Nodes have a position attribute for canvas rendering. Edges have source and target handles for typed connections.

### Node Types

| Type | Description | Channel Interaction |
|------|-------------|---------------------|
| `llm` | LLM inference node | Reads messages/tools → writes response |
| `code` | Code execution node (sandboxed) | Reads input → writes output |
| `condition` | Conditional branch node | Reads state → routes to branch |
| `tool` | Tool invocation node | Reads tool_name/args → writes result |
| `agent` | Sub-Agent node (references another Agent) | Maps parent state → child state → returns result |
| `subgraph` | Subgraph node (nested Workflow) | Maps outer state → inner state → returns result |
| `input` | Workflow input node | Receives external input |
| `output` | Workflow output node | Outputs final result |

---

## Tool

A Tool is an executable capability that an Agent can invoke. Tools come from three sources:

- **Built-in** — Shipped with Hecate (code execution, file operations, web search, etc.)
- **Custom** — User-defined via API Schema (HTTP endpoints wrapped as tools)
- **MCP** — Discovered from connected MCP servers via the Model Context Protocol

Each tool has a JSON Schema for input parameters and output, a risk level (LOW/MEDIUM/HIGH/CRITICAL), and an approval scope (once/session/project/global). These security attributes govern when and how the tool can be invoked automatically versus requiring human approval.

---

## Skill

Skills are reusable instruction packages that extend an Agent's capabilities on demand. A Skill is defined in SKILL.md format (compatible with Claude Code's skill format) and includes:

- **instructions** — The SKILL.md body containing agent instructions
- **allowed_tools** — List of tools the skill is permitted to use
- **scripts** — Executable script paths
- **references** — Reference document paths
- **max_tokens** — Maximum token budget for the skill content

Skills are discovered from three sources with a clear priority hierarchy:

1. **System-level** — Platform built-in skills
2. **User-level** — `~/.hecate/skills/` directory
3. **Project-level** — `{project}/.skills/` directory

Skills are loaded on demand — an Agent doesn't consume token budget for skills it isn't actively using.

---

## Knowledge Base

A Knowledge Base is a collection of documents that an Agent can search via RAG (Retrieval-Augmented Generation). Each Knowledge Base specifies an embedding model, chunk strategy (auto/fixed/semantic), chunk size, and overlap.

The document processing pipeline: **Document upload** → **Docling parsing** → **Text chunking** (512-1024 tokens) → **BGE-M3 embedding** (dense + sparse vectors) → **Qdrant hybrid index**.

Each Document has a parsing status (pending/parsing/completed/failed) and contains multiple Chunks. A Chunk stores the text content, metadata (page number, position, title), and the embedding vector.

---

## Memory System

Hecate implements a four-level memory architecture, inspired by cognitive science models:

### L1: Working Memory

Named blocks in the agent's context window — analogous to short-term memory. Each block has a label (e.g., "persona", "user_profile", "domain_context"), content, position, and token limit. Agents can read and edit their own working memory blocks during execution.

### L2: Conversation Memory

Conversation history within a single session. Includes an auto-compression pipeline that activates when the context window fills: **snip** (remove old messages) → **microcompact** (summarize groups of messages) → **autocompact** (generate a running summary). This allows long conversations without exceeding token limits.

### L3: User Memory

Cross-session persistent facts about users and their preferences. Extracted automatically from conversations using a Mem0-style approach: the system identifies factual statements, encodes them as embeddings, and stores them with scope (user + agent + session), type (semantic/procedural/episodic), and importance scores. Retrieval uses multi-signal fusion ranking.

### L4: Knowledge Memory

Equivalent to the RAG pipeline — structured knowledge from documents stored in Knowledge Bases. This level is not separately labeled as "memory" in the UI; it's accessed through the normal RAG retrieval flow.

---

## Session and Conversation

A **Conversation** represents a chat thread between a user and an Agent. A **Session** is a single execution context within a conversation — it tracks the agent's current position in the workflow, the latest checkpoint, and the execution status.

Session lifecycle states:

- **active** — Currently executing
- **interrupted** — Paused via `interrupt()`, waiting for human input
- **completed** — Execution finished normally
- **failed** — Execution ended with an error

A **Message** is an individual entry in a conversation (system/user/assistant/tool role) with content, optional tool calls, and metadata (token usage, model, latency).

---

## Knowledge Graph (Planned)

The **Knowledge Graph** provides structured, entity-centric knowledge representation that complements the vector-based RAG pipeline. While RAG retrieves text chunks by semantic similarity, the Knowledge Graph captures typed entities and their relationships, enabling multi-hop reasoning and structured retrieval.

### Graph Entities

| Entity | Description | Key Fields |
|--------|-------------|------------|
| **GraphEntity** | A typed node in the knowledge graph | `id`, `kb_id`, `type`, `name`, `properties` (JSONB), `embedding` (vector) |
| **GraphRelation** | A typed edge connecting two entities | `id`, `kb_id`, `source_id`, `target_id`, `type`, `properties` (JSONB), `weight` |
| **Community** | A cluster of related entities detected by graph algorithms | `id`, `kb_id`, `entity_ids` (list), `summary` (text), `algorithm` (Louvain/Leiden) |

### GraphStore ABC

The `GraphStore` abstract base class defines the contract for graph database backends:

- **Neo4jGraphStore** — Production backend with Cypher query language, full-text search, and graph algorithms (PageRank, Louvain community detection)
- **InMemoryGraphStore** — Default backend for development and testing using adjacency lists

### Extraction Pipeline

Documents are processed through an LLM-powered extraction pipeline:

```
Document → Chunk → LLM Entity Extraction → LLM Relation Extraction
                                                      │
                                          Entity Resolution (dedup)
                                                      │
                                          GraphStore.add_entities()
                                          GraphStore.add_relations()
```

### GraphRAG

Community detection (Louvain/Leiden) clusters related entities into communities, enabling **GraphRAG** — retrieval at the community level for broader context. This provides better answers for "big picture" questions than chunk-level retrieval alone.

See [ADR-017: Knowledge Graph Architecture](adr/017-knowledge-graph-architecture.md).

---



The **Ontology Action System** extends the knowledge graph with executable operations. An Action defines a set of changes to objects, properties, and relationships that an agent (or human) can invoke.

Action types:

- **Simple Actions** — Update a single property value
- **Compound Actions** — Modify multiple objects in one transaction
- **External Actions** — Write back to source systems (ERP, CRM, etc.)
- **LLM-Backed Actions** — Use LLM to determine action parameters

Execution modes:

- **Manual** — Agent proposes action, human approves before execution
- **Automatic** — Agent executes action directly (for low-risk operations)
- **Conditional** — Action executes only if conditions are met

See [ADR-014: Ontology Action System](adr/014-ontology-action-system.md).

---

## Ontology-Augmented Generation (Planned)

**OAG (Ontology-Augmented Generation)** evolves the RAG pipeline by combining retrieval, logic, and actions into a complete reasoning loop:

1. **Retrieval** (existing RAG) — find relevant knowledge
2. **Logic** (ontology functions) — apply business rules and reasoning
3. **Actions** (ontology actions) — execute decisions and write back

OAG grounds LLM reasoning in a structured knowledge model with executable actions, enabling agents to not just retrieve information but also act on it. See [ADR-015: Ontology-Augmented Generation](adr/015-ontology-augmented-generation.md).

---

## Decision Lineage (Planned)

**Decision Lineage** records the complete chain of reasoning behind every agent action: who (human or agent) decided what, based on which data version, at what time, and with what outcome. This provides auditability for compliance and enables feedback-driven learning.

Decision lineage captures:

- **Who** initiated the decision (human, agent, or automation)
- **What** action was taken
- **When** it was executed
- **Which** data version was used (knowledge graph snapshot)
- **Why** the decision was made (reasoning trace)
- **Outcome** of the action (success, failure, rollback)

---

## Agentic RL Framework (Planned)

The **Agentic RL Framework** implements a data flywheel for agent self-optimization:

1. **Trace Collection** — Production traces from EventStore
2. **Labeling** — Auto-labeling (LLM-as-Judge) + human annotation
3. **Training Data** — Prompt optimization datasets + RL training sets
4. **Optimization** — Prompt self-optimization (ACE/GEPA) + optional model fine-tuning
5. **Validation** — A/B testing against baseline + canary release

See [ADR-013: Agentic RL Framework](adr/013-agentic-rl-framework.md).

---

## Prompt

Prompts are versioned template strings with variable interpolation. Each Prompt has a name, template body, variable list, version number, and labels (production/staging/development). This enables A/B testing and staged rollout of prompt changes.

---

## Agent Card (A2A Protocol)

An **Agent Card** is a JSON document served at `/.well-known/agent.json` that serves as a digital business card for an agent. It declares the agent's identity, capabilities, skills, supported input/output formats, and security schemes. External agents fetch the card to understand what the remote agent can do before delegating tasks.

Agent Card fields:

- **name** — Agent display name
- **description** — What the agent does (used for capability matching)
- **url** — A2A service endpoint
- **version** — Protocol version
- **skills** — List of capabilities with descriptions
- **authentication** — Supported security schemes (APIKey, HTTPAuth, OAuth2, OpenIdConnect, MutualTLS)

Signed Agent Cards (A2A v1.0) include cryptographic signatures that verify the card was issued by the domain owner, preventing card impersonation attacks.

---

## Task Lifecycle (A2A Protocol)

A **Task** is the unit of work in A2A agent-to-agent communication. When a client agent delegates work to a remote agent, the interaction is wrapped in a Task with a well-defined state machine:

```
submitted → working → completed
                    → failed
                    → cancelled
```

- **submitted** — Task received by remote agent
- **working** — Remote agent is actively processing
- **completed** — Task finished successfully, artifacts available
- **failed** — Task ended with an error
- **cancelled** — Task cancelled by client or human

Tasks produce **Artifacts** (tangible deliverables) and support streaming updates via SSE for long-running operations. The task lifecycle enables both synchronous (immediate) and asynchronous (hours/days) agent collaboration patterns.

---

## Zero Trust Identity (Planned)

Hecate's identity model will evolve from a single-tier (API Key or JWT) to a **Two-Tier Identity Model** that distinguishes application-level identity from end-user-level identity.

### Two-Tier Identity Model

| Tier | Token Type | Scope | Use Case |
|------|-----------|-------|----------|
| **App-level** | API Key (`hcat_*`) | Application identity | Server-to-server integration, CI/CD pipelines |
| **User-level** | JWT (Bearer) | End-user identity | Interactive sessions, per-user audit |

Both tiers can be combined: an App-level API Key carries a User-level JWT to represent "application X acting on behalf of user Y," enabling granular access control and dual audit trails.

### Per-Token-Type Auth Pipeline

Different token types route through separate authentication pipelines with distinct verification steps:

- **JWT Pipeline** — Verify HS256 signature + expiry + RBAC scope
- **APIKey Pipeline** — Verify SHA-256 hash + rate limit + edition gating
- **PAT Pipeline** — Verify Personal Access Token scope + rotation policy
- **OAuth SSO Pipeline** — Verify OIDC discovery + scope mapping

### Zero Trust Principles

- **Per-agent identity**: Each agent has a unique identity with scoped permissions (principle of least privilege)
- **Token exchange**: OAuth 2.0 Token Exchange (RFC 8693) for identity propagation across service boundaries
- **Continuous verification**: Every tool call, LLM invocation, and knowledge query is authenticated — no implicit trust based on network position

See [ADR-018: Zero Trust Identity Architecture](adr/018-zero-trust-identity-architecture.md).

---



Versionable resources — Agents, Workflows, Prompts, and Skills — share a unified version management mechanism. Each version captures a complete configuration snapshot, a change summary, and the operator who made the change. Previous versions are preserved for rollback and audit.

---

## Storage Design

### Database Table Mapping

| Entity | Table Name | Key Fields |
|--------|-----------|------------|
| Agent | `agents` | id, workspace_id, name, persona, model_config (JSONB), mode |
| Workflow | `workflows` | id, workspace_id, name, version, nodes (JSONB), edges (JSONB) |
| Tool | `tools` | id, workspace_id, name, source, parameters (JSONB), risk_level |
| Skill | `skills` | id, name, source, description, instructions (TEXT), allowed_tools (JSONB) |
| KnowledgeBase | `knowledge_bases` | id, workspace_id, name, embedding_model, chunk_strategy |
| Document | `documents` | id, kb_id, filename, file_type, parsing_status |
| Chunk | `chunks` | id, document_id, content, metadata (JSONB), embedding (vector) |
| MemoryBlock | `memory_blocks` | id, agent_id, label, content, position, limit |
| Memory | `memories` | id, content, scope (JSONB), type, importance, embedding (vector) |
| Conversation | `conversations` | id, agent_id, user_id |
| Message | `messages` | id, conversation_id, role, content, tool_calls (JSONB), metadata (JSONB) |
| Session | `sessions` | id, conversation_id, agent_id, status, current_node, checkpoint_id |
| Checkpoint | `checkpoints` | id, session_id, node_id, channel_state (JSONB), metadata (JSONB) |
| Prompt | `prompts` | id, workspace_id, name, template, version, labels (JSONB) |
| Organization | `organizations` | id, name, settings (JSONB) |
| User | `users` | id, org_id, email, name, role |
| Workspace | `workspaces` | id, org_id, name, settings (JSONB) |
| ResourceVersion | `resource_versions` | resource_type, resource_id, version, snapshot (JSONB), change_summary |
| GraphEntity | `graph_entities` *(planned)* | id, kb_id, type, name, properties (JSONB), embedding (vector) |
| GraphRelation | `graph_relations` *(planned)* | id, kb_id, source_id, target_id, type, properties (JSONB), weight |
| Community | `graph_communities` *(planned)* | id, kb_id, entity_ids (JSONB), summary (TEXT), algorithm |

### Design Conventions

- UUID primary keys throughout, supporting distributed ID generation
- JSONB columns for flexible metadata and configuration storage
- Soft delete via `deleted_at` timestamp
- Tenant isolation via `workspace_id` foreign keys
- Alembic manages schema evolution

### Notable Aliases

Several models use field aliases to avoid collisions with SQLAlchemy and Pydantic reserved attributes:

- **`model_config_db`** — The ORM column is named `model_config` (via `mapped_column`), but Pydantic uses `model_config_db` to avoid Pydantic's reserved `model_config` attribute.
- **`metadata_`** — Five models use `metadata_` in Python (with trailing underscore) mapped to `metadata` in SQL, to avoid SQLAlchemy's reserved `metadata` attribute.

---

## Further Reading

| Document | Description |
|----------|-------------|
| [Architecture](architecture.md) | System overview, design principles, module architecture |
| [Engine Design](engine-design.md) | Pregel runtime, compiler, channels, checkpoints, streaming |
| [RAG Pipeline Design](rag-pipeline-design.md) | Document ingestion, embedding, hybrid search pipeline |
| [Security Architecture](security-architecture.md) | Guardrail hooks, PII anonymization, auth, audit trail |
| [Knowledge & Memory Design](knowledge-memory-design.md) | Memory P5 target state, Knowledge Graph integration |
| [ADR Directory](adr/) | Architecture Decision Records |
