# Knowledge & Memory Design

> Deep dive into Hecate's knowledge management and memory system: RAG pipeline, knowledge graph, ontology system, and multi-level memory architecture. For a system overview, see [Architecture](architecture.md). For engine details, see [Engine Design](engine-design.md). For the architecture decisions behind these enhancements, see [ADR-024](adr/024-knowledge-memory-enhancement.md).

> **Status note (2026-09-22)**: 4.21 (Task Memory + Tool Memory + KM6 Work Context Graph) and 4.23 (Cross-Thread Memory Store) are **shipped**. The `memory-storage-family` change introduces the new tables (`episodes`, `reflections`, `reflection_runs`, `work_context_nodes`, `work_context_edges`), extends `memories` / `knowledge_memories` with `team_id` + `actor_id` namespace columns, extends the `MemoryProvider` contract with tier-4 / tier-5 capabilities plus `end_episode` / `escalate_failure` lifecycle hooks, ships the `ReflectionEngine` (sister to the consolidation engine), and adds two new agent tools (`reflection_search` / `work_context_query`). All surfaces are gated on `REFLECTION_ENABLED=false` by default so the pre-change behavior is byte-identical. See the [Task Memory](#task-memory-421), [Cross-Thread Memory Store](#cross-thread-memory-store-423), and [Work Context Graph KM6](#work-context-graph-km6) sections below for the design summary; full requirements live under `openspec/changes/memory-storage-family/specs/`.

---

## Overview

Hecate's Knowledge & Memory system provides agents with the ability to ingest, store, retrieve, and reason over enterprise knowledge. The system is built on three pillars:

1. **RAG Pipeline** — Document ingestion, embedding, and retrieval
2. **Knowledge Graph** — Structured entity/relationship modeling with ontology extensions
3. **Memory System** — Multi-level agent memory (L1-L4 + advanced memory types)

![Knowledge & Memory L2 Architecture](images/knowledge-memory-l2.png)

---

## RAG Pipeline

### Ingestion Pipeline

```
Document → Parser → Chunker → Embedding → Vector Store
```

- **Document Parser** (`parser.py`): Docling-based parsing for PDF, DOCX, MD, TXT, HTML
- **Text Chunker** (`chunker.py`): Fixed-size chunking (1000 chars default, 200 overlap) with sentence-boundary awareness
- **Embedding Service** (`embedding.py`): BGE-M3 model producing dense (1024-dim) + sparse (lexical) vectors
- **Vector Store** (`vector_store.py`): Pluggable abstraction (Qdrant, Chroma) with RRF fusion fallback

### Retrieval Pipeline

```
Query → Embedding → [Dense Search + Sparse Search] → RRF Fusion → Results → Citations
```

- **Hybrid Search** (`searcher.py`): Combines dense (cosine similarity, weight 0.7) and sparse (BM25, weight 0.3) retrieval
- **RRF Fusion** (`vector_store.py`): Reciprocal Rank Fusion with k=60 for combining results
- **Citations** (`types.py`): OpenAI-compatible citation format with source tracking

### Planned Enhancements

| Feature | Description |
|---------|-------------|
| GraphRAG Query Engine | Global/Local/Hybrid search using knowledge graph |
| Agentic RAG | Iterative retrieval with query reformulation |
| Multimodal RAG | Image, audio, video content processing |
| Cloud Document Connector | Block structuring, AI preprocessing for cloud docs |

---

## Knowledge Graph

### Core Concepts

The Knowledge Graph models entities and their relationships:

- **Entity**: A real-world object (person, place, concept) with properties
- **Relationship**: A typed connection between two entities
- **Property**: Key-value metadata on entities or relationships
- **Community**: A cluster of densely connected entities (via Leiden algorithm)

### Planned Components

| Feature | Description |
|---------|-------------|
| Knowledge Graph Construction | LLM-based entity/relationship extraction from documents |
| Graph Database Integration | GraphStore ABC with Neo4j + in-memory backends |
| Community Detection | Leiden algorithm + community summaries |
| Knowledge Graph Visualization | Interactive graph browser with node expand/collapse |
| Knowledge Graph UI Editor | Visual graph maintenance UI |

---

## Ontology System

The Ontology System extends the Knowledge Graph with formal schema definitions and executable actions.

### Ontology Schema

- **Class Hierarchies**: Inheritance (e.g., `Employee` is-a `Person`)
- **Property Constraints**: Domain, range, cardinality validation
- **Relationship Types**: Typed connections with constraints
- **Schema Format**: JSON Schema definition aligned with KG Construction

### Ontology Actions

Actions define operations on ontology objects:

- **Simple Actions**: Update a single property value
- **Compound Actions**: Modify multiple objects in one transaction
- **External Actions**: Write back to source systems
- **LLM-Backed Actions**: Use LLM to determine action parameters

Execution modes: Manual (human approval), Automatic (direct execution), Conditional (on conditions met)

### Ontology Augmented Generation (OAG)

OAG evolves RAG by combining retrieval + logic + actions:

```
Query → Retrieval (RAG) + Logic (Rules/ML) + Actions (Write-Back) → Response
```

### Planned Components

| Feature | Description |
|---------|-------------|
| Ontology Schema Definition | Class hierarchies, property constraints, relationship types |
| SHACL Validation | W3C SHACL-based graph data quality validation |
| Ontology Import/Export | OWL 2/RDF/JSON-LD format support |
| Ontology Versioning | Version snapshots, diff, backward compatibility |
| Ontology Action System | Actions for modifying objects, Agent executes via Action Tool |
| Decision Lineage | Record who decided what based on which data version |
| OAG | RAG + Logic + Actions complete closed loop |

---

## Memory System

### Architecture Overview

```
┌─────────────────────────────────────────────────┐
│              Agent Context Window                │
│  ┌───────────────────────────────────────────┐  │
│  │ L1 Working Memory (MemoryBlock)           │  │
│  │ persona / user_profile / domain_context   │  │
│  └───────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────┐  │
│  │ L2 Conversation Memory                    │  │
│  │ snip → microcompact → autocompact         │  │
│  │ Memory Pressure Alert (4.17)              │  │
│  └───────────────────────────────────────────┘  │
├─────────────────────────────────────────────────┤
│              Persistent Storage                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐│
│  │ L3 User  │ │ Task     │ │ Tool Memory      ││
│  │ Memory   │ │ Memory   │ │                  ││
│  └──────────┘ └──────────┘ └──────────────────┘│
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐│
│  │ L4 Know  │ │ Conv     │ │ Cross-Thread     ││
│  │ Memory   │ │ Recall   │ │ Store            ││
│  └──────────┘ └──────────┘ └──────────────────┘│
├─────────────────────────────────────────────────┤
│              Memory Management                  │
│  • LLM-Managed Memory (4.16)                    │
│  • Self-Editing Memory (4.19)                   │
│  • Retrieval Escalation (4.20)                  │
│  • Memory Versioning (4.24)                     │
│  • Auto-Integration (4.5)                       │
│  • Flush + Lifecycle (4.25)                     │
│  • Policies + Governance (4.16)                 │
└─────────────────────────────────────────────────┘
```

### Memory Levels

| Level | Type | Scope | Implementation |
|-------|------|-------|----------------|
| **L1** | Working Memory | Current session | Named blocks in context window (MemoryBlock) |
| **L2** | Conversation Memory | Current session | Auto-compression pipeline (snip/microcompact/autocompact) |
| **L3** | User Memory | Cross-session | Persistent facts with vector retrieval |
| **L4** | Knowledge Memory | Cross-session | RAG-backed knowledge archive |

### Advanced Memory Types

| Type | Description |
|------|-------------|
| Task Memory | Learn from task execution trajectories (success/failure patterns) |
| Tool Memory | Record tool usage experience (parameter tuning, best practices) |
| Cross-Thread Store | Independent store for cross-session user preferences and shared knowledge |
| Episodic Memory | Scenario-based memory for conversation context |

### Memory Management Features

| Feature | Description |
|---------|-------------|
| LLM-Managed Memory ✅ | Agent manages memory via built-in tools behind `MEMORY_TOOLS_ENABLED`; tiered `MemoryProvider` contract lets third-party backends take over; per-workspace/per-agent `memory_policies` narrow the tool surface (permission fields narrow-only, numeric fields hard-cap clamped) and drive flush/lifecycle parameters |
| Memory Flush (pre_compaction) ✅ | Best-effort registration at the L2 compaction boundary (`consolidation_flush_windows`) hands the to-be-dropped window to the consolidation trigger bus for async extraction (at-least-once, watermark-idempotent, pressure-flag priority); correctness anchored on ADR-033 surface replacement — the raw event log is never deleted by compaction, so a missed flush degrades to a later sweep, never data loss; `MEMORY_FLUSH_ENABLED` gates it |
| Memory Lifecycle ✅ | Per-layer TTL expiry (anchor `last_confirmed_at`, L4 never by default), capacity eviction scored on the fusion value-score family (protection window + per-sweep budget), actor→workspace promotion gate (three thresholds, sharing-ceiling bounded, off unless enabled); archives are soft-delete with lifecycle `memory_edit_log` reasons and restorable via the governance API/UI; `MEMORY_LIFECYCLE_ENABLED` gates it |
| Governance REST + Memory Center ✅ | `/api/memory/governance/*`: edit-log and consolidation/reflection run queries, policy CRUD with resolved view, archive/restore, lifecycle stats, recall search; Studio Memory Center browses L3/L4/recall, archives/restores, and edits policies — content edits stay on the agent tool path |
| Memory Pressure Alert | Context threshold notification to LLM for memory consolidation |
| Self-Editing Memory ✅ | Layered edit semantics: exact replace (ambiguity refused) / line insert / whole-block rethink; L3/L4 corrections via `memory_update`/`memory_forget` with `revision` optimistic concurrency + `memory_edit_log` audit; ADD-only supersession deferred to the temporal-memory workstream |
| Retrieval Escalation ✅ (was Multi-Step Retrieval) | Weak/empty memory searches inject one debounced `[memory_hint]` block guiding reformulation/cursor/`exclude_session_ids` iteration; the MemGPT-heartbeat framing is retired |
| Memory Versioning | Version snapshots, diff, rollback capability |
| Memory Importance Scoring | Score memories by access frequency, time decay, semantic relevance |
| Multi-Signal Fusion Retrieval | Combine vector similarity + time decay + importance + frequency |
| Conversation Recall Storage ✅ | Transcript-level recall layer: `recall_messages` + Qdrant `hecate_recall`, background indexer over message-channel events (version watermarks, idempotent), `conversation_search` with time-window/roles/cursor/exclusion; outlives event retention |
| Memory Clustering | Clustering and graph-structured memory organization |

---

## Temporal Memory & Reasoning (3.5.13)

### Problem

Hecate's memory system stores facts without temporal metadata. Queries like "Where did the user live before SF?" or "What was the project status last month?" cannot be answered correctly because the system has no concept of when facts were valid or when they were superseded. Mem0 v2.0 reports +29.6 benchmark points from adding temporal reasoning.

### Architecture

```
Memory Store
    │
    ▼
┌──────────────────────────────────────────────────┐
│  Temporal Metadata Enrichment                    │
│  Each memory record gains:                       │
│    valid_from: datetime                           │
│    valid_to: datetime | None (None = current)    │
│    superseded_by: UUID | None                     │
│    temporal_confidence: float (0-1)               │
└──────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────┐
│  Temporal Query Intent Extraction                 │
│  Query → Temporal intent classification:          │
│    PRESENT → rank valid_to=None highest           │
│    PAST    → rank superseded facts by valid_to    │
│    COMPARISON → diff facts across time periods    │
└──────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────┐
│  Temporal Retrieval Ranking                       │
│  Fuse: semantic score × temporal relevance ×     │
│  importance score                                 │
└──────────────────────────────────────────────────┘
```

### Fact Supersession

When a new fact contradicts an existing one:
1. Old fact: `valid_to = now`, `superseded_by = new_id`
2. New fact: `valid_from = now`, `valid_to = None`
3. Both preserved — query intent determines which surfaces

ADD-only semantics (Mem0 v2.0 pattern): memories accumulate, nothing is overwritten.

### Data Model

```python
class TemporalMetadata:
    valid_from: datetime
    valid_to: datetime | None       # None = currently valid
    superseded_by: UUID | None      # Link to replacement fact
    temporal_confidence: float      # 0-1, decays for fast-changing domains
```

---

## Lazy GraphRAG (3.5.14)

### Problem

Full GraphRAG requires upfront entity extraction, relationship inference, community detection, and community summary generation for the entire corpus. For large enterprise deployments (>100K pages), this is cost-prohibitive. Microsoft LazyGraphRAG runs at ~0.1% of full GraphRAG indexing cost while matching quality at ~4% of query cost.

### Progressive Enrichment Pipeline

```
Stage 0 (Initial Index — ~0.1% of full GraphRAG cost):
  Document → Lightweight NER (spaCy) → Concept Hash → Flat Entity Index
  No community detection. No LLM-extracted relationships.

Stage 1 (Query-triggered — per-subgraph):
  Query → Entity Lookup → Subgraph Expansion
  → On-demand LLM relationship extraction for query neighborhood
  → On-demand mini-community summary

Stage 2 (Progressive enrichment — background):
  Frequently-queried subgraphs → full community summaries
  Popular paths converge toward full GraphRAG quality
  Cold paths remain at Stage 0/1 (cost-appropriate)
```

### Enrichment State Tracking

```python
class SubgraphEnrichmentState:
    subgraph_hash: str          # Hash of entity set
    enrichment_level: int       # 0=NER-only, 1=on-demand, 2=full-community
    query_count: int            # How many queries touched this subgraph
    last_enriched_at: datetime
    community_summary: str | None
```

### Design Principle

Cost is proportional to usage. Cold corpora stay cheap; hot subgraphs converge to full GraphRAG quality. Enrichment is idempotent and incremental.

---

## Sleep-time Memory Consolidation (4.5 Enhancement)

### Problem

Memory Integration (4.5) performs basic background deduplication and forgetting, but lacks scheduled synthesis. Letta's sleep-time compute and Perplexity Brain's overnight synthesis demonstrate that batch memory review during idle periods produces higher-quality "learned context" for the next session.

### Consolidation Cycle

```
Trigger (configurable schedule, default: 02:00 daily)
    │
    ▼
┌─────────────────────────────────────────────┐
│  Consolidation Subagent (background)         │
│  1. Review conversation history              │
│     (since last consolidation)               │
│  2. Extract durable facts                    │
│  3. Score by importance + novelty            │
│  4. Update memory blocks                     │
│     (add new, supersede old with temporal)   │
│  5. Clean up stale entries                   │
│     (low importance + old + unused)          │
│  6. Produce "learned context" diff           │
└─────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────┐
│  Memory Store (atomic write)                 │
│  + ConsolidationLog (audit trail)            │
└─────────────────────────────────────────────┘
```

### Subagent Isolation

The consolidation subagent has write access to memory store but cannot execute tools or call LLMs beyond the consolidation prompt. This prevents uncontrolled side effects.

---

## DRIFT Search Mode (3.5.4 Enhancement)

### Problem

GraphRAG Query Engine (3.5.4) has three modes: Global (community summaries), Local (entity neighborhood), Hybrid (vector + graph fusion). Microsoft GraphRAG's DRIFT search combines entity fanout with community context, providing focused multi-hop reasoning that neither pure Local nor pure Global can achieve.

### DRIFT Algorithm

1. Extract topic entities from query
2. Fan out to entity neighbors (like Local Search)
3. At each hop, check if neighbors belong to a community (like Global Search)
4. If community has a summary, include it in context
5. Prune branches outside query-relevant communities
6. Continue until sufficient context or max depth reached

### Search Mode Comparison

| Mode | Strategy | Best For |
|------|----------|----------|
| Global | Community summary map-reduce | Holistic corpus questions |
| Local | Entity neighborhood traversal | Specific entity questions |
| Hybrid | Vector + graph fusion | General-purpose queries |
| **DRIFT** | **Entity fanout + community pruning** | **Multi-hop with community awareness** |

---

## Schema-Aware Graph Traversal (3.5.10 Enhancement)

### Problem

Standard GraphRAG traversal uses semantic similarity to guide search. In dense enterprise KGs, high-degree attribute nodes ("semantic supernodes" like `Status: Active` connected to 10,000 entities) cause uncontrolled search expansion. SCAIR (ACL 2026) demonstrates that schema constraints must prune the search space BEFORE semantic scoring.

### Structure-First Retrieval

```
Query → Extract topic entities
    │
    ▼
┌──────────────────────────────────────────┐
│  SHACL Shape Lookup                       │
│  For each entity type, retrieve:          │
│    - Allowed outgoing relationship types  │
│    - Cardinality constraints              │
│    - Property domains/ranges              │
└──────────────────────────────────────────┘
    │
    ▼ (only schema-valid paths traversed)
┌──────────────────────────────────────────┐
│  Traversal Guards                         │
│  Block: paths to high-degree attribute    │
│    nodes unless explicitly queried        │
│  Allow: paths through typed relationships │
│    matching query intent                  │
└──────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────┐
│  Semantic Scoring (post-traversal)        │
│  Score remaining schema-valid paths       │
└──────────────────────────────────────────┘
```

---

## Work Context Graph (4.21 Enhancement)

### Problem

Task Memory (4.21) records task execution trajectories but as flat records. Perplexity Brain (June 2026) demonstrates that a structured work memory graph — tracking what worked, what failed, corrections made, and source reliability — improves task correctness by 25% and reduces cost by 13%.

### Work Memory Graph Structure

```
                    ┌──────────┐
            ┌──────│ Method A │────── success_rate: 0.8
            │       └──────────┘       usage_count: 15
            │                │         last_used: 2026-07-01
            │         ┌──────┘
            ▼         ▼
    ┌──────────┐  ┌──────────┐
    │ Outcome  │  │Correction│────── user_correction_count: 3
    │ Success  │  │ "Use B"  │
    └──────────┘  └──────────┘
            │         │
            │  ┌──────┘
            ▼  ▼
    ┌──────────┐
    │ Method B │────── success_rate: 0.95 (improved after correction)
    └──────────┘       usage_count: 22
```

### Node Types

| Type | Purpose | Key Fields |
|------|---------|-----------|
| **method** | An approach tried for a task | success_rate, usage_count, last_used_at |
| **outcome** | Result of applying a method | success/fail, quality_score |
| **correction** | User feedback correcting agent's approach | correction_count, corrected_at |
| **source** | Information source used | reliability (0-1), last_verified |
| **pattern** | Recurring task pattern | frequency, typical_methods |

### Self-Improving Cycle

1. **Task starts**: Query graph for similar past tasks → retrieve methods, outcomes, corrections
2. **Task executes**: Record methods tried, outcomes, corrections in real-time
3. **Task completes**: Update node scores (success_rate, usage_count, source_reliability)
4. **Next task**: Graph is richer → better starting context

---

## Integration with Agent Engine

The Knowledge & Memory system integrates with the Agent Engine through:

- **EnginePort.knowledge_query()**: RAG retrieval during LLM execution
- **EnginePort.memory_store/retrieve()**: Memory operations during agent execution
- **ContextEngine**: Context assembly with memory and knowledge retrieval
- **Guardrail Hooks**: Security filtering on retrieved content

## Task Memory (4.21)

The Task Memory stack complements the existing fact-memory (L3 / L4) and RAG (L4 knowledge) layers by capturing **what the agent did and what it learned from doing it**, rather than what was said or what is true about the world. The `memory-storage-family` change ships three coupled surfaces:

- **Episodes** (`episodes` table). One row per user-goal-level task. The runtime writes tool calls as `TOOL` events into `actions` and tool results into `outcomes`; the agent runtime calls `episode_close(episode_id)` at task boundaries to stamp `closed_at`. Only closed episodes enter the reflection candidate pool.
- **Reflections** (`reflections` table). Typed records produced by the `ReflectionEngine` (sister to the existing `ConsolidationEngine`) over closed episodes. Each reflection has `title / use_cases / hints / source_episode_ids / confidence / status / isrel / issup / isuse`. Status flow: `pending → approved | rejected | deprecated`. Reflections with `status='approved'` are surfaced by `reflection_search` and consumed by the consumer paths.
- **Tool Memory (4.22)** — same store, different strategy. Tool calls are written as `TOOL` events into the parent episode's `actions` rather than a dedicated store. The reflection engine analyzes `actions` + `outcomes` together when extracting typed reflections, so the tool experience is implicitly captured without a second storage layer.

The four quality gates (per ADR-024 §6 reflection hygiene) protect against reflection pollution:

1. **Model isolation** — the reflection LLM call only sees episode + knowledge_memory read paths; write tools are excluded.
2. **LLM-as-Judge** — every reflection carries three quality tokens `isrel / issup / isuse`; all three must clear 0.5 to pass.
3. **`source_episode_ids >= 2`** — single-episode reflections are refused at gate 1 (no LLM call needed) and re-checked at gate 3.
4. **Confidence auto-deprecation** — `confidence < 0.4` three times in a row flips status to `deprecated`. Driven by `ConfidenceEvaluator` on an hourly cadence.

All four gates plus the LLM seam cost are contained behind the `REFLECTION_ENABLED` master flag (default off). Off paths are byte-identical to the pre-change platform surface: no new tables touched at runtime, no `reflection_search` / `work_context_query` tools seeded, no `sync_turn` episode-record sub-action fired.

## Cross-Thread Memory Store (4.23)

Cross-Thread extends the existing two-layer namespace (`workspace_id + user_id`) on `memories` and `knowledge_memories` to four layers: `workspace_id + team_id + actor_id + session_id`. The migration (`m4_23_namespace_team_actor`) backfills `actor_id` from the existing `user_id` column on `knowledge_memories` and from `scope->>'user_id'` on `memories`; `team_id` starts as `null` for all rows.

`memory_add` and `memory_search` accept an explicit `scope` parameter (`actor_scoped` default, `workspace_shared` opt-in). Workspace-shared writes require the calling user to hold workspace-admin role (carried on the tool execution context as `workspace_role='admin'`); editor-role callers receive a structured `permission_denied` error before any DB write.

The four-layer namespace visibility is enforced in `KnowledgeMemoryService.search_knowledge` via the `_namespace_visible` helper, applied to the Qdrant vector payload mirror. The composite index `(workspace_id, team_id, actor_id)` is added in the same migration to keep namespace scans cheap.

## Work Context Graph (KM6)

The Work Context Graph is the structured successor to flat Task Memory records (ADR-024 §6). When a reflection is approved, the `ReflectionEngine._apply` calls `work_context_graph.create_node_for_reflection` in the same transaction (atomic with the reflection insert). The node's `node_type` is derived heuristically from the reflection's `title` + `hints` content (`method / outcome / correction / source / pattern`).

When a reflection is `operator='update'` (same title, new evidence), the prior reflection's graph nodes are flipped `active=False` and a `corrected_by` edge is written between the prior and new nodes — both in the same transaction. Background aggregation (`NodeStatsAggregator.run`) recomputes `usage_count` / `success_rate` / `last_used_at` / `user_correction_count` from the reflection lineage on a slow cadence (default: nightly). Aggregator writes are bulk, never blocking the read path.

The `work_context_query` agent tool reads `active=true` nodes only; superseded nodes remain queryable for audit. KM6 is the substrate the `Work Context Graph (4.21 Enhancement)` section of the design doc describes — it is now implemented and shipped rather than "proposed".

---

## Further Reading

| Document | Description |
|----------|-------------|
| [ADR-024: Knowledge & Memory Enhancement](adr/024-knowledge-memory-enhancement.md) | Architecture decisions for KM1-KM6 |
| [Architecture](architecture.md) | System overview, module architecture |
| [RAG Pipeline Design](rag-pipeline-design.md) | RAG pipeline deep dive |
| [Engine Design](engine-design.md) | Pregel runtime, worker pool |
| [ADR-017: Knowledge Graph Architecture](adr/017-knowledge-graph-architecture.md) | GraphStore ABC and community detection |
| [ADR-014: Ontology Action System](adr/014-ontology-action-system.md) | Action system for ontology |
| [ADR-015: OAG](adr/015-ontology-augmented-generation.md) | Ontology-Augmented Generation |
| [ADR-006: Four-Level Memory](adr/006-four-level-memory.md) | L1-L4 memory architecture |
