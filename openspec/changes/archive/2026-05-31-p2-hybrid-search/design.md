## Context

Hecate's RAG retrieval layer currently only implements 3.2.1 (dense vector retrieval). The core code lives under `src/hecate/services/rag/`:

- **`EmbeddingService`** — Uses BAAI/bge-m3 model; `EmbeddingResult` already has a `sparse` field but it always returns an empty `{}`
- **`QdrantIndexer`** — `create_collection()` only configures dense vectors; `search()` only does ANN search
- **`HybridSearcher`** — The class name suggests hybrid retrieval, but `search()` only calls dense retrieval; `sparse_weight` is never used
- **`KnowledgeBaseService`** — `search()` delegates to `HybridSearcher`; `ingest_document()` only stores dense vectors
- **`EnginePort.knowledge_query`** — Abstract interface defined, but `AgentExecutionPort` implementation directly raises `NotImplementedError`

Tech stack constraints:
- Qdrant 1.12+ (already installed), natively supports sparse vectors + `QueryRequest` fusion API
- BGE-M3 model (FlagEmbedding) can generate both dense and sparse vectors simultaneously
- Python 3.12+, SQLAlchemy 2.0 async, FastAPI

## Goals / Non-Goals

**Goals:**
- Implement 3.2.2 keyword retrieval: BM25-style retrieval based on Qdrant sparse vectors
- Implement 3.2.3 hybrid retrieval: dense + sparse score fusion with configurable weights
- Make `EnginePort.knowledge_query` actually usable
- Maintain backward compatibility: existing knowledge bases can use new retrieval without migration

**Non-Goals:**
- No standalone BM25 engine (e.g., Elasticsearch) — use Qdrant native approach
- No reranking — this is P4 3.2.5
- No frontend UI changes — retrieval capability changes are transparent to the frontend
- No incremental sparse vector updates — all vectors generated at first index time

## Decisions

### Decision 1: Qdrant Native Sparse Vectors vs Standalone BM25 Library

**Choice: Qdrant native sparse vectors**

Rationale:
- Already have `qdrant-client>=1.12.0`, zero new dependencies
- Qdrant natively supports `SparseVectorParams` + `QueryRequest` fusion, no need to manually implement score fusion
- Single storage engine, simpler operations
- Performance: Qdrant uses inverted index internally for sparse vectors, approaching native BM25 performance

**Alternative (rejected):**
- `rank-bm25` pure Python — needs manual in-memory inverted index, not persistent, not scalable
- Elasticsearch — heavyweight dependency, high operational cost, overlaps with Qdrant functionality

### Decision 2: Sparse Vector Generation Method

**Choice: BGE-M3 native sparse output**

Rationale:
- BGE-M3 is a multilingual model supporting dense + sparse + ColBERT vectors simultaneously
- FlagEmbedding library (already installed) `BGEM3FlagModel` can directly output sparse embeddings
- Sparse vector format: `dict[int, float]` (token_id → weight), directly storable in Qdrant SparseVector

**Alternative (rejected):**
- `fastembed` sparse models — needs extra dependency, and BGE-M3 is already sufficient
- Manual BM25 tokenization — high complexity, inferior to BGE-M3 sparse output

### Decision 3: Score Fusion Strategy

**Choice: Qdrant built-in Fusion (RRF) with optional weighting**

Rationale:
- Qdrant's `QueryRequest` supports `prefetch` + `fusion`, doing fusion at the database layer
- RRF (Reciprocal Rank Fusion) is an industry standard, no score normalization needed
- Future extensibility via `QueryRequest` weight parameters for weighted fusion

**Alternative (rejected):**
- Application-layer manual RRF — adds code complexity, can't leverage Qdrant optimizations
- Simple weighted sum — needs score normalization; different retrieval methods have different score distributions

### Decision 4: EnginePort Integration Approach

**Choice: Inject KnowledgeBaseService into AgentExecutionPort**

Rationale:
- `AgentExecutionPort` is already the concrete implementation of `EnginePort`
- `KnowledgeBaseService` is the RAG entry point service, includes the search method
- After injection, `knowledge_query` directly delegates to `KnowledgeBaseService.search`

## Risks / Trade-offs

| Risk | Impact | Mitigation |
|------|--------|------------|
| BGE-M3 sparse vector memory usage | Each document stores both dense (4KB) + sparse (1KB) vectors | Monitor memory, batch indexing if needed |
| Qdrant fusion API stability | Relatively new feature, may have bugs | Prepare fallback: application-layer RRF |
| Existing knowledge base migration | Old collections have no sparse vectors | Create new collections and reindex, or lazy migration |
| BGE-M3 load time | First model load is slow | Keep existing lazy loading + model caching |
