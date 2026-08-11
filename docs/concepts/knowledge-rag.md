# Knowledge and Retrieval

An LLM is trained on a snapshot of the public internet and freezes there. **Retrieval-Augmented Generation (RAG)** is how you ground the model in *your* data — product manuals, internal wikis, policy documents, support tickets — without retraining. Upload a document, and the agent retrieves the relevant passages at query time, cites them in the response, and never pretends to know things it cannot look up.

Hecate's RAG pipeline is a two-phase system: **ingestion** turns documents into searchable vectors, **retrieval** runs at query time and feeds results into the agent's context window. Understanding the pipeline helps you choose chunk sizes, pick a vector backend, decide between `hybrid`/`dense`/`sparse` search modes, and predict what your agent will actually cite.

> RAG is the **L4 Knowledge Memory** in Hecate's [four-level memory architecture](memory.md#l4-knowledge-memory) — reference material that lives outside the context window until the agent needs it.

---

## The two phases

```
INGESTION (per document, once)                    RETRIEVAL (per query)
┌─────────────────────────────────────┐           ┌─────────────────────────────┐
│ Document upload                     │           │ User query                  │
│   → DocumentParser (Docling)        │           │   → EmbeddingService.encode │
│   → TextChunker (1000/200, sentence)│           │       (dense + sparse)      │
│   → EmbeddingService (BGE-M3)       │           │   → HybridSearcher          │
│       dense (1024) + sparse (BM25)  │           │       (mode = hybrid)       │
│   → VectorStore.upsert              │           │   → RRF fusion (k=60)       │
│       (Qdrant / Chroma)             │           │   → Citation objects        │
└─────────────────────────────────────┘           └─────────────────────────────┘
```

---

## Ingestion

### Document parsing

`DocumentParser` (`services/rag/parser.py`) extracts text from uploaded files using [Docling](https://github.com/DS4SD/docling) as the primary engine. Supported formats: **PDF** (layout-aware), **Microsoft Word (.docx)**, **Markdown (.md)**, **plain text (.txt)**, **HTML**. When Docling is not installed, the parser falls back to basic text extraction. URL-based ingestion is handled separately by `WebCrawler` (`services/rag/crawler.py`).

### Text chunking

`TextChunker` (`services/rag/chunker.py`) splits the parsed text into embeddable chunks:

| Parameter | Default | Effect |
|-----------|---------|--------|
| `chunk_size` | 1000 chars | Maximum chunk length |
| `chunk_overlap` | 200 chars | Overlap between adjacent chunks (preserves context across boundaries) |

**Sentence-boundary aware splitting** — when a chunk would end mid-sentence, the chunker looks for the nearest sentence boundary (period or newline) within the second half of the chunk and adjusts the break point. Cutting sentences in half degrades retrieval quality; this prevents it. Each chunk becomes a `Chunk` dataclass with `content`, `index`, `start_char`, `end_char`, `metadata`.

### Embedding

`EmbeddingService` (`services/rag/embedding.py`) generates **dual-vector embeddings** using [BGE-M3](https://huggingface.co/BAAI/bge-m3) (`BAAI/bge-m3` via FlagEmbedding):

| Vector | Dimension | Purpose |
|--------|-----------|---------|
| **Dense** | 1024 | Semantic similarity (cosine distance) — matches paraphrases and synonyms |
| **Sparse** | `{token_id: weight}` | Lexical matching (BM25-style) — matches exact keywords, IDs, codes |

The model is lazy-loaded on first use. When FlagEmbedding is not installed, the service falls back to a deterministic hash-based mock embedding so development works without the GPU dependency. Each encode call returns `EmbeddingResult(dense=..., sparse=...)`.

### Indexing

`KnowledgeBaseService.ingest_document()` (`services/rag/service.py`) orchestrates the full pipeline and persists results through the `VectorStore` ABC:

```
Document → parse() → text
         → chunk_text() → [Chunk, Chunk, ...]
         → encode() → [EmbeddingResult, ...]
         → store.upsert(ids, dense_vectors, sparse_vectors, payloads)
```

Each chunk's payload includes the text content, chunk metadata (page number, position, source file), and `workspace_id` for [tenant isolation](#tenant-isolation). Source files themselves are stored in MinIO via `MinIOStorage` (`services/rag/storage.py`).

---

## Retrieval

### Hybrid search

`HybridSearcher` (`services/rag/searcher.py`) combines dense and sparse retrieval. Three modes:

| Mode | Mechanism | Use case |
|------|-----------|----------|
| `hybrid` (default) | Dense + Sparse → fusion | General purpose — best recall |
| `dense` | Dense vector similarity only | Semantic matching (synonyms, paraphrasing) |
| `sparse` | Sparse lexical matching only | Exact keyword matching (IDs, codes, names) |

In `hybrid` mode, the searcher runs three queries in parallel — `search_hybrid`, `search_dense`, `search_sparse` — and returns each result annotated with its `dense_score` and `sparse_score` for debugging. Default fusion weights: dense `0.7`, sparse `0.3`.

### Reciprocal Rank Fusion

The `VectorStore` base class (`services/rag/vector_store.py`) provides an application-layer **Reciprocal Rank Fusion (RRF)** fallback with `DEFAULT_RRF_K = 60`:

```
RRF_score(d) = Σ 1 / (k + rank_i(d))
```

Documents appearing in both dense and sparse result lists get summed scores; documents in only one list get a single contribution. This guarantees that vector backends without native hybrid search (Chroma) still provide hybrid retrieval quality.

### Citations

Results are converted to `Citation` objects (`services/rag/types.py`) carrying `position`, `kb_id`, `kb_name`, `document_name`, `chunk_id`, `score`, and `content_snippet`. The `to_annotation()` method renders citations as `kb_citation` annotations — the OpenAI-compatible format that lets frontends render source references inline in chat responses.

---

## Vector store backends

Two backends ship with Hecate, selected via `settings.VECTOR_STORE_TYPE` and the `get_vector_store()` factory (`services/rag/factory.py`):

| Backend | File | Native hybrid | Sparse support | Use |
|---------|------|:---:|:---:|-----|
| **Qdrant** | `qdrant_store.py` | ✅ (Prefetch + `Fusion.RRF`) | ✅ | Production |
| **Chroma** | `chroma_store.py` | ❌ (application-layer RRF fallback) | ❌ | Local development |

`QdrantVectorStore` sets `supports_hybrid = True` and overrides `search_hybrid()` to use Qdrant's native prefetch+fusion query. `ChromaVectorStore` inherits the default RRF fallback and logs a warning when sparse mode is requested.

---

## Tenant isolation

Every search operation accepts an optional `workspace_id` parameter. When provided, it is added as a payload filter to vector store queries — agents in one workspace cannot retrieve chunks from another workspace's knowledge base, even if collection names collide. This is the same `workspace_id` isolation pattern used across Hecate's [multi-tenancy model](multi-tenancy.md).

---

## Integration with the agent engine

The RAG pipeline is accessed by the execution engine via one method on `EnginePort`:

```python
async def knowledge_query(self, query: str, kb_ids: list[UUID]) -> list[dict]
```

When an LLM node in a graph has associated knowledge bases, the engine:

1. Reads the agent's `knowledge_bases` configuration.
2. Calls `HybridSearcher.search()` with the user's query.
3. Prepends retrieved chunks to the LLM context as system messages.
4. Passes citations through to the response as annotations.

Knowledge retrieval is **transparent to the Graph DSL** — there is no `knowledge-retrieval` node required. The `knowledge-retrieval` *node type* exists for explicit retrieval steps in workflows, but bind a knowledge base to a `conversation` node and retrieval happens automatically.

---

## Choosing what to use

| You want to... | Use |
|----------------|-----|
| Ground an agent in your documents | Create a Knowledge Base, upload docs, bind it to the agent |
| Maximize recall on general queries | Default `hybrid` search mode |
| Match exact keywords (product codes, IDs) | `sparse` mode |
| Match paraphrases and semantic equivalents | `dense` mode |
| Run production RAG at scale | Qdrant backend (`VECTOR_STORE_TYPE=qdrant`) |
| Develop locally without Docker | Chroma backend (in-process) |
| Run the same query three ways for debugging | `KnowledgeBaseService.compare_search_modes()` returns dense/sparse/hybrid side-by-side |

---

## Further reading

- [Memory System](memory.md) — RAG is L4 Knowledge Memory in the four-level architecture
- [Multi-Tenancy](multi-tenancy.md) — the `workspace_id` isolation that governs knowledge base access
- [Extension Points](../reference/extension-points.md) — the `EnginePort.knowledge_query` method signature
- [RAG Pipeline Design](../design/rag-pipeline-design.md) — full L3 breakdown, GraphRAG/DRIFT/lazy-indexing roadmap
- [Knowledge & Memory Design](../design/knowledge-memory-design.md) — knowledge graph, ontology system, four-level memory target state
- [ADR-017: Knowledge Graph Architecture](../design/adr/017-knowledge-graph-architecture.md) — the `GraphStore` ABC for the planned KG layer
- [Knowledge Base and RAG tutorial](../tutorials/02-knowledge-base.md) — hands-on end-to-end
