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
              │              ├── KnowledgeBase ─── Document ─── Chunk
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

## Prompt

Prompts are versioned template strings with variable interpolation. Each Prompt has a name, template body, variable list, version number, and labels (production/staging/development). This enables A/B testing and staged rollout of prompt changes.

---

## Resource Versioning

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
| [ADR Directory](adr/) | Architecture Decision Records |
