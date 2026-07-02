# ADR-024: Knowledge & Memory Enhancement Architecture

> **Status**: Proposed
> **Date**: 2026-07-02

## Context

Hecate's Knowledge & Memory system delivers a full RAG pipeline (Docling parser, BGE-M3 embedding, hybrid search, RRF fusion), 4-level memory (L1-L4), context engineering (6 components), knowledge graph (construction, Neo4j integration, community detection), and ontology system (schema, SHACL, actions, OAG). Competitive analysis against Mem0 v2.0, Letta/MemGPT, Perplexity Brain, Microsoft GraphRAG, LightRAG, Google Agentic RAG, and SCAIR revealed 6 gaps:

| Gap | Description | Type | Priority |
|-----|-------------|------|----------|
| KM1 | **Temporal Memory & Reasoning** — time-aware memory with valid_from/valid_to, temporal query support | New Feature | P4 (3.5.13) |
| KM2 | **Lazy GraphRAG** — cost-optimized graph indexing (~0.1% of full GraphRAG) | New Feature | P4 (3.5.14) |
| KM3 | **Sleep-time Memory Consolidation** — scheduled overnight synthesis with background subagents | 4.5 Enhancement | P4 |
| KM4 | **DRIFT Search Mode** — entity fanout + community context as 4th GraphRAG search mode | 3.5.4 Enhancement | P4 |
| KM5 | **Schema-Aware Graph Traversal** — SHACL constraints as traversal guards in GraphRAG | 3.5.10 Enhancement | P4 |
| KM6 | **Work Context Graph** — evolve Task Memory into self-improving work memory | 4.21 Enhancement | P4 |

These gaps span three layers:
1. **Memory intelligence layer** — Temporal reasoning, sleep-time consolidation, work context graph
2. **Retrieval cost layer** — Lazy indexing, DRIFT search, schema-aware traversal
3. **Data/metadata layer** — Temporal metadata on memories, progressive enrichment state

## Decision

### 1. Temporal Memory & Reasoning (KM1/3.5.13) — Time-Aware Memory

Build temporal memory as a **metadata enrichment layer** on existing memory models. Each memory record gains temporal fields:

```python
class TemporalMetadata:
    valid_from: datetime       # When this fact became true
    valid_to: datetime | None  # None = currently valid; set when superseded
    superseded_by: UUID | None # Link to the newer fact that replaced this one
    temporal_confidence: float # 0-1, decays for old facts in fast-changing domains
```

**Retrieval ranking**: At query time, extract temporal intent from the query:
- **Present tense** ("Where does X live?") → rank current facts (valid_to=None) highest
- **Past tense** ("Where did X live before?") → rank superseded facts by recency of valid_to
- **Temporal comparison** ("What changed since Y?") → diff facts with valid_from > Y

**Fact supersession**: When a new fact contradicts an existing one, the old fact gets `valid_to=now, superseded_by=new_id`, and the new fact gets `valid_from=now`. Both are preserved — queries choose which to surface based on temporal intent.

**Design principle**: ADD-only semantics (inspired by Mem0 v2.0). Memories accumulate; nothing is overwritten. Temporal metadata handles the "current vs historical" distinction at query time.

### 2. Lazy GraphRAG (KM2/3.5.14) — Progressive Index Enrichment

Build Lazy GraphRAG as a **progressive enrichment pipeline** that starts cheap and converges toward full GraphRAG quality over time:

```
Stage 0 (Initial Index — ~0.1% cost):
  Document → Lightweight NER (spaCy) → Concept Hash → Flat Entity Index
  No community detection, no LLM-extracted relationships

Stage 1 (Query-triggered — per-subgraph):
  Query → Entity Lookup → Subgraph Expansion → On-demand community summary
  LLM extracts relationships only for entities in the query neighborhood

Stage 2 (Progressive enrichment — background):
  Frequently-queried subgraphs accumulate full community summaries
  Popular paths converge toward full GraphRAG quality
  Cold paths remain at Stage 0/1 (cost-appropriate)
```

**Progressive state tracking**:
```python
class SubgraphEnrichmentState:
    subgraph_hash: str         # Hash of entity set
    enrichment_level: int      # 0=NER-only, 1=on-demand, 2=full-community
    query_count: int           # How many queries touched this subgraph
    last_enriched_at: datetime
    community_summary: str | None  # Populated at level 2
```

**Design principle**: Cost is proportional to usage. Cold corpora stay cheap; hot subgraphs converge to full GraphRAG quality. Enrichment is idempotent and incremental.

### 3. Sleep-time Memory Consolidation (KM3/4.5 Enhancement) — Overnight Synthesis

Extend Memory Integration (4.5) with a **scheduled consolidation cycle** using background subagents:

```
Trigger (configurable schedule, default: 02:00 daily)
    │
    ▼
┌─────────────────────────────────────────┐
│  Consolidation Subagent (background)     │
│  ┌───────────────────────────────────┐   │
│  │ 1. Review conversation history     │   │
│  │    (since last consolidation)      │   │
│  │ 2. Extract durable facts           │   │
│  │ 3. Score by importance + novelty   │   │
│  │ 4. Update memory blocks            │   │
│  │    (add new, supersede old)        │   │
│  │ 5. Clean up stale entries          │   │
│  │    (low importance + old + unused) │   │
│  │ 6. Produce "learned context" diff  │   │
│  └───────────────────────────────────┘   │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│  Memory Store (atomic write)             │
│  + ConsolidationLog (audit trail)        │
└─────────────────────────────────────────┘
```

**Subagent isolation**: The consolidation subagent has write access to memory store but cannot execute tools or call LLMs beyond the consolidation prompt. This prevents uncontrolled side effects.

**Design principle**: Consolidation is **additive and auditable**. Every change is logged. Old memories are superseded (not deleted) with temporal metadata. Users can review what the consolidation changed.

### 4. DRIFT Search Mode (KM4/3.5.4 Enhancement) — Hybrid Graph Traversal

Add DRIFT (Dynamic Reasoning and Inference with Faithful Traversal) as the 4th search mode in GraphRAG Query Engine:

| Mode | Strategy | When to Use |
|------|----------|-------------|
| Global | Community summary map-reduce | Holistic questions about the entire corpus |
| Local | Entity neighborhood traversal | Questions about specific entities |
| Hybrid | Vector + graph fusion | General-purpose queries |
| **DRIFT** | **Entity fanout + community context** | **Multi-hop reasoning needing both specific entities and their community context** |

DRIFT algorithm:
1. Extract topic entities from query
2. Fan out to entity neighbors (like Local Search)
3. At each hop, check if neighbors belong to a community (like Global Search)
4. If community has a summary, include it in context
5. Prune branches that fall outside query-relevant communities
6. Continue until sufficient context or max depth reached

**Design principle**: DRIFT combines the precision of Local Search with the context-awareness of Global Search. Community-aware pruning prevents the uncontrolled expansion that plagues pure entity fanout in dense graphs.

### 5. Schema-Aware Graph Traversal (KM5/3.5.10 Enhancement) — Structure-First Retrieval

Integrate SHACL ontology constraints into GraphRAG traversal as **structural gates**:

```
Query: "Which components are affected by supplier X?"
    │
    ▼
┌─────────────────────────────────────────┐
│  Schema Constraint Check (pre-traversal)│
│  SHACL shape: Component → suppliedBy →  │
│  Supplier (cardinality: 1..*)           │
│  Allowed traversal: Component →         │
│  suppliedBy → Supplier only             │
│  Blocked: Component → status → *        │
│  (status is a high-degree attribute     │
│   node — semantic supernode)            │
└─────────────────────────────────────────┘
    │
    ▼ (only schema-valid paths traversed)
┌─────────────────────────────────────────┐
│  Semantic Scoring (post-traversal)      │
│  Score remaining paths by relevance     │
└─────────────────────────────────────────┘
```

**Design principle**: Structure-first retrieval. Schema constraints prune the search space BEFORE semantic scoring. This prevents "semantic supernodes" (e.g., `Status: Active` connected to 10,000 entities) from causing uncontrolled search expansion — a problem identified by SCAIR (ACL 2026) in real enterprise CMDB deployments.

### 6. Work Context Graph (KM6/4.21 Enhancement) — Self-Improving Work Memory

Evolve Task Memory (4.21) from a flat record of task trajectories into a **structured work memory graph**:

```python
class WorkContextNode:
    node_type: str  # "method" | "outcome" | "correction" | "source" | "pattern"
    content: str
    success_rate: float          # For methods: historical success rate
    usage_count: int             # How many times this method was tried
    last_used_at: datetime
    user_correction_count: int   # How many times user corrected this
    source_reliability: float    # For sources: 0-1 reliability score

class WorkContextEdge:
    edge_type: str  # "tried_before" | "led_to" | "corrected_by" | "validated_by"
    weight: float   # Confidence in this connection
```

**Self-improving cycle**:
1. **Task starts**: Query Work Context Graph for similar past tasks → retrieve relevant methods, outcomes, corrections
2. **Task executes**: Record methods tried, outcomes, user corrections in real-time
3. **Task completes**: Update node scores (success_rate, usage_count, source_reliability)
4. **Next task**: Graph is richer → better starting context

**Design principle**: Work Context Graph remembers what the AGENT did (not just what the USER said). This follows Perplexity Brain's insight: "AI memory about the user serves engagement; work memory serves performance."

## Architecture Diagram

```
                         ┌─────────────────────────────────────┐
                         │          Agent Engine                │
                         │    (PregelRuntime + Workers)         │
                         └──────────────┬──────────────────────┘
                                        │
                         ┌──────────────▼──────────────────────┐
                         │          EnginePort                  │
                         │    knowledge_query / memory ops      │
                         └──────────────┬──────────────────────┘
                                        │
           ┌────────────────────────────┼────────────────────────────┐
           │                            │                            │
    ┌──────▼──────┐            ┌───────▼───────┐            ┌──────▼──────┐
    │  RAG Layer  │            │  Memory Layer  │            │  KG Layer   │
    │             │            │                │            │             │
    │ Hybrid Srch │            │ L1-L4 Memory   │            │ Graph Store │
    │ Reranking   │            │ + Temporal (KM1)│           │ Community   │
    │ GraphRAG    │            │ + Consolidation│            │ Detection   │
    │   Global    │            │   (KM3)        │            │             │
    │   Local     │            │ + Work Context │            │ Ontology    │
    │   Hybrid    │            │   Graph (KM6)  │            │ + SHACL     │
    │   DRIFT(KM4)│            │                │            │ + Schema    │
    │   Lazy(KM2) │            │                │            │   Aware(KM5)│
    └─────────────┘            └────────────────┘            └─────────────┘
           │                            │                            │
           └────────────────────────────┼────────────────────────────┘
                                        │
                    ┌───────────────────▼───────────────────┐
                    │   Sleep-time Consolidation (KM3)       │
                    │   Background subagent:                 │
                    │   review → extract → update → clean    │
                    │   (configurable schedule, default 02:00)│
                    └─────────────────────────────────────────┘
```

## Consequences

### Positive

- **Temporal query support**: Agents can answer "before/after/current" questions correctly (+29.6 points benchmark from Mem0)
- **Cost-efficient large-scale GraphRAG**: Lazy indexing enables GraphRAG on >100K page corpora where full indexing is cost-prohibitive
- **Self-improving agent performance**: Work Context Graph compounds value over time — each completed task enriches the graph, improving future task starting points (+25% correctness from Perplexity Brain)
- **Enterprise-grade KG reasoning**: Schema-aware traversal prevents the semantic supernode problem that causes standard GraphRAG to fail on dense enterprise KGs (SCAIR, ACL 2026)
- **Overnight memory quality**: Sleep-time consolidation ensures agents start each day with a cleaner, richer memory state

### Negative

- **Temporal metadata overhead**: Every memory record gains 4 fields; storage cost increases ~15% for memory-heavy workloads
- **Progressive enrichment complexity**: Lazy GraphRAG introduces a multi-level state machine for subgraph enrichment; debugging requires tracking which level a subgraph is at
- **Background consolidation latency**: Sleep-time consolidation runs on a schedule (default overnight); real-time memory updates during the day are not consolidated until the next cycle

## Related Documents

- [Knowledge & Memory Design](../knowledge-memory-design.md) — Detailed design for KM1-KM6 with personas, API endpoints, and data models
- [ADR-017: Knowledge Graph Architecture](017-knowledge-graph-architecture.md) — GraphStore ABC and community detection foundation
- [ADR-015: Ontology-Augmented Generation](015-ontology-augmented-generation.md) — OAG closed-loop reasoning
- [ADR-006: Four-Level Memory](006-four-level-memory.md) — L1-L4 memory architecture
- [ADR-022: Model Hub Enhancement](022-model-hub-enhancement.md) — Parallel enhancement pattern
- [ADR-023: Tool Platform Enhancement](023-tool-platform-enhancement.md) — Parallel enhancement pattern
