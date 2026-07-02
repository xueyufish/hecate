# RAG Pipeline Design

> Deep dive into the Retrieval-Augmented Generation pipeline: document ingestion, chunking, embedding, hybrid search, citation system, and planned enhancements for GraphRAG, DRIFT search, lazy indexing, temporal memory, and schema-aware traversal. For a system overview, see [Architecture](architecture.md). For security aspects of RAG, see [Security Architecture](security-architecture.md). For enhancement decisions, see [ADR-024](adr/024-knowledge-memory-enhancement.md).

---

## Overview

Hecate's RAG pipeline provides knowledge retrieval for agents — converting uploaded documents into searchable vector embeddings, then retrieving relevant passages at query time to ground LLM responses with factual context.

The pipeline has two phases:

- **Ingestion**: Document → Parse → Chunk → Embed → Index
- **Retrieval**: Query → Embed → Hybrid Search (Dense + Sparse) → RRF Fusion → Ranked Results → Citations

![RAG Pipeline L3](images/rag-pipeline-l3.png)

---

## Ingestion Pipeline

### Document Parsing (`parser.py`)

The `DocumentParser` extracts text from uploaded files. It uses [Docling](https://github.com/DS4SD/docling) as the primary parsing engine, supporting:

- PDF (with layout-aware extraction)
- Microsoft Word (.docx)
- Markdown (.md)
- Plain text (.txt)
- HTML

When Docling is not installed, the parser falls back to basic text extraction. Web crawling is handled separately by `crawler.py` for URL-based ingestion.

### Text Chunking (`chunker.py`)

The `TextChunker` splits extracted text into chunks suitable for embedding:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `chunk_size` | 1000 chars | Maximum chunk length |
| `chunk_overlap` | 200 chars | Overlap between adjacent chunks |

**Sentence-boundary aware splitting**: When the end of a chunk falls mid-sentence, the chunker looks for the nearest sentence boundary (period or newline) within the second half of the chunk. If found, it adjusts the break point to that boundary. This prevents cutting sentences in half, which would degrade retrieval quality.

Each `Chunk` dataclass captures: `content`, `index`, `start_char`, `end_char`, and `metadata`.

### Embedding (`embedding.py`)

The `EmbeddingService` generates dual-vector embeddings using [BGE-M3](https://huggingface.co/BAAI/bge-m3) (`BAAI/bge-m3`):

| Vector Type | Dimension | Purpose |
|-------------|-----------|---------|
| Dense | 1024 | Semantic similarity (cosine distance) |
| Sparse | Variable (token_id → weight) | Lexical matching (BM25-style) |

**Configuration:**
- Batch size: 32 texts per encode call
- Max sequence length: 512 tokens
- FP16: Disabled (FP32 for compatibility)

The model is lazy-loaded on first use. When FlagEmbedding is not installed, a mock embedding service generates deterministic hash-based vectors for development/testing.

```python
@dataclass
class EmbeddingResult:
    dense: list[float]      # 1024-dim dense vector
    sparse: dict[int, float]  # {token_id: weight} sparse vector
```

### Vector Store Indexing

Embeddings are persisted via the `VectorStore` abstraction (see [Vector Store Layer](#vector-store-layer) below). The `KnowledgeBaseService.ingest_document()` method orchestrates the full ingestion:

```
Document → parse() → text
         → chunk_text() → [Chunk, Chunk, ...]
         → encode() → [EmbeddingResult, ...]
         → store.upsert(ids, dense_vectors, sparse_vectors, payloads)
```

Each chunk's payload includes: text content, chunk metadata (page number, position, source file), and optional `workspace_id` for tenant isolation filtering.

---

## Retrieval Pipeline

### Hybrid Search (`searcher.py`)

The `HybridSearcher` combines dense and sparse retrieval for optimal relevance. Three search modes are supported:

| Mode | Mechanism | Use Case |
|------|-----------|----------|
| `hybrid` (default) | Dense + Sparse → RRF fusion | General purpose — best recall |
| `dense` | Dense vector similarity only | Semantic matching (synonyms, paraphrasing) |
| `sparse` | Sparse lexical matching only | Exact keyword matching (IDs, codes, names) |

**Score weights** (hybrid mode): Dense = 0.7, Sparse = 0.3

### Reciprocal Rank Fusion (`vector_store.py`)

When the vector store backend does not support native hybrid search, the `VectorStore` base class provides an application-layer RRF (Reciprocal Rank Fusion) fallback:

```
RRF_score(d) = Σ 1 / (k + rank_i(d))
```

Where `k = 60` (per Cormack et al. 2009), and `rank_i(d)` is the 1-based rank of document `d` in result list `i`. Documents appearing in both dense and sparse results get summed scores; documents in only one list get a single contribution.

This ensures that backends without native hybrid search (e.g., Chroma) still provide hybrid retrieval quality.

### Citation System (`types.py`)

Search results are converted to `Citation` objects with OpenAI-compatible annotation format:

```python
class Citation:
    position: int          # 1-indexed rank in retrieved context
    kb_id: UUID            # Knowledge base identifier
    kb_name: str           # Knowledge base display name
    document_name: str     # Source document filename
    chunk_id: str          # Vector store chunk ID
    score: float           # Relevance score from hybrid search
    content_snippet: str   # First 150 characters of chunk content
```

The `to_annotation()` method converts citations to the `kb_citation` annotation type, enabling frontend rendering of source references in chat responses.

---

## Vector Store Layer

### Abstraction (`vector_store.py`)

The `VectorStore` ABC defines the contract for all vector database backends:

| Method | Purpose |
|--------|---------|
| `create_collection(name, dim, sparse_dim)` | Initialize a collection with dense + sparse support |
| `upsert(collection, ids, vectors, sparse_vectors, payloads)` | Insert or update chunks |
| `search(collection, query_vector, limit, filter)` | Dense vector similarity search |
| `search_sparse(collection, sparse_vector, limit, filter)` | Sparse lexical search |
| `search_hybrid(collection, dense, sparse, limit, filter)` | Native hybrid search (if supported) |
| `delete(collection, ids)` | Remove chunks |

The base class provides a default `search_hybrid` implementation using RRF fusion of `search()` + `search_sparse()` results, so backends only need to implement the primitive operations.

### Backends

| Backend | File | Native Hybrid | Sparse Support | Production Ready |
|---------|------|:---:|:---:|:---:|
| **Qdrant** | `qdrant_store.py` | ✅ | ✅ | ✅ |
| **Chroma** | `chroma_store.py` | ❌ (RRF fallback) | ❌ | Development |

### Factory (`factory.py`)

The `get_vector_store()` factory selects the backend based on configuration (`settings.VECTOR_STORE_TYPE`). It caches the singleton instance to avoid repeated connection setup.

---

## Tenant Isolation

All search operations accept an optional `workspace_id` parameter. When provided, it is added as a payload filter to vector store queries, ensuring that agents in one workspace cannot retrieve chunks from another workspace's knowledge base — even if collection names collide.

---

## Integration with Agent Engine

The RAG pipeline is accessed by the execution engine via the `EnginePort.knowledge_query()` method. When an LLM node in a Graph has associated knowledge bases, the engine:

1. Retrieves the agent's `knowledge_bases` configuration
2. Calls `HybridSearcher.search()` with the user's query
3. Prepends retrieved chunks to the LLM context as system messages
4. Passes citations through to the response as annotations

This integration is transparent to the Graph DSL — knowledge retrieval is a capability provided by the service layer, not a node type in the engine.

---

## Planned Enhancements

The RAG pipeline will evolve beyond single-shot vector retrieval to support structured knowledge, multi-step reasoning, closed-loop execution, and cost-optimized graph indexing.

### Knowledge Graph & GraphRAG

A **Knowledge Graph** with `GraphStore` ABC (Neo4j + in-memory backends) will complement vector-based retrieval. LLM-powered entity/relation extraction populates typed entities and relationships. Community detection (Leiden) clusters related entities, enabling **GraphRAG** — community-level retrieval that provides broader context for "big picture" questions. See [ADR-017](adr/017-knowledge-graph-architecture.md).

**GraphRAG Query Engine (3.5.4)** will support four search modes:

| Mode | Strategy | Use Case |
|------|----------|----------|
| Global | Community summary map-reduce | Holistic questions about entire corpus |
| Local | Entity neighborhood traversal | Questions about specific entities |
| Hybrid | Vector + graph traversal fusion | General-purpose queries |
| **DRIFT** (KM4) | **Entity fanout + community context** | **Multi-hop reasoning with community-aware pruning** |

DRIFT search (KM4) extracts topic entities from the query, fans out to entity neighbors (like Local Search), and at each hop checks if neighbors belong to a community — including community summaries when available. Community-aware pruning prevents uncontrolled expansion in dense graphs.

### Lazy GraphRAG (3.5.14)

Full GraphRAG requires upfront entity extraction, relationship inference, community detection, and community summary generation for the entire corpus. For large enterprise deployments (>100K pages), this is cost-prohibitive.

**Lazy GraphRAG** uses a progressive enrichment pipeline:

```
Stage 0 (Initial — ~0.1% of full GraphRAG cost):
  Document → Lightweight NER (spaCy) → Concept Hash → Flat Entity Index
  No community detection. No LLM-extracted relationships.

Stage 1 (Query-triggered — per-subgraph):
  Query → Entity Lookup → Subgraph Expansion
  → On-demand LLM relationship extraction
  → On-demand mini-community summary

Stage 2 (Progressive — background):
  Frequently-queried subgraphs → full community summaries
  Cold paths remain at Stage 0/1 (cost-appropriate)
```

Cost is proportional to usage. Cold corpora stay cheap; hot subgraphs converge to full GraphRAG quality. Enrichment is idempotent and incremental.

### Schema-Aware Graph Traversal (3.5.10 Enhancement)

Standard GraphRAG traversal uses semantic similarity to guide search. In dense enterprise KGs, high-degree attribute nodes ("semantic supernodes" like `Status: Active` connected to 10,000 entities) cause uncontrolled search expansion.

**Schema-aware traversal** integrates SHACL/ontology constraints as structural gates:

```
Query → Extract topic entities
    │
    ▼
SHACL Shape Lookup: allowed relationship types per entity type
    │
    ▼ (only schema-valid paths traversed)
Traversal Guards: block paths to high-degree attribute nodes
    │
    ▼
Semantic Scoring: score remaining schema-valid paths
```

Structure-first retrieval: schema constraints prune the search space BEFORE semantic scoring. This prevents semantic supernodes from causing uncontrolled expansion — a problem identified by SCAIR (ACL 2026) in real enterprise CMDB deployments.

### Ontology-Augmented Generation (OAG)

**OAG** combines three layers into a closed-loop reasoning system:

1. **Retrieval** (existing RAG) — find relevant knowledge
2. **Logic** (ontology functions) — apply business rules and reasoning
3. **Actions** (ontology actions) — execute decisions and write back to source systems

This enables agents to not just retrieve information but also act on it, with full decision lineage for compliance. See [ADR-015](adr/015-ontology-augmented-generation.md) and [ADR-014](adr/014-ontology-action-system.md).

### Agentic RAG

**Agentic RAG** transforms retrieval from single-shot search to multi-step retrieval with reasoning:

- **Iterative retrieval**: Agent retrieves, evaluates, and retrieves again based on intermediate results
- **Query decomposition**: Complex questions are broken into sub-queries, each retrieved independently
- **Self-correction**: Agent evaluates retrieval quality and reformulates queries if results are insufficient
- **Cross-source fusion**: Combines vector search, graph traversal, and structured queries

### Advanced Retrieval Techniques

Planned retrieval enhancements to improve relevance and recall:

- **HyDE** (Hypothetical Document Embedding) — Generate a hypothetical answer, embed it, and use it for retrieval
- **Multi-Query** — LLM generates multiple query variants, retrieves for each, and fuses results
- **Reranking** — Cross-encoder reranking of initial retrieval results for precision
- **Contextual Compression** — LLM-based compression of retrieved chunks to extract only relevant portions

---

## Further Reading

| Document | Description |
|----------|-------------|
| [ADR-024: Knowledge & Memory Enhancement](adr/024-knowledge-memory-enhancement.md) | Architecture decisions for KM1-KM6 (temporal memory, lazy GraphRAG, DRIFT search, schema-aware traversal, sleep-time consolidation, work context graph) |
| [Knowledge & Memory Design](knowledge-memory-design.md) | P5 target state for memory system, KG integration, ontology system, detailed KM1-KM6 sections |
| [Architecture](architecture.md) | System overview, module architecture |
| [Security Architecture](security-architecture.md) | PII masking, guardrail hooks for RAG security |
| [Core Concepts](concepts.md) | KnowledgeBase, Document, Chunk, Knowledge Graph entity definitions |
| [Engine Design](engine-design.md) | EnginePort interface for knowledge_query, Knowledge Graph integration |
| [ADR-015: OAG](adr/015-ontology-augmented-generation.md) | Ontology-Augmented Generation decision record |
| [ADR-017: Knowledge Graph](adr/017-knowledge-graph-architecture.md) | GraphStore ABC + Neo4j architecture decision |
