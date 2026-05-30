## 1. Sparse Vector Generation (EmbeddingService)

- [x] 1.1 Update `embedding.py` — `encode()` uses BGE-M3's `encode()` method with `return_dense=True, return_sparse=True`, converts sparse output to `dict[int, float]`
- [x] 1.2 Update `encode_query()` — calls `encode([query])` and returns `EmbeddingResult` containing both dense and sparse
- [x] 1.3 Update `_mock_embedding()` — generate deterministic mock sparse vectors (token_id → weight mapping based on text hash)

## 2. Qdrant Sparse Vector Support (QdrantIndexer)

- [x] 2.1 Update `create_collection()` — add `sparse_vectors_config={"sparse": SparseVectorParams(index=models.SparseIndexParams())}` parameter
- [x] 2.2 Update `upsert_vectors()` — add optional parameter `sparse_vectors: list[dict[int, float]] | None = None`, pass both dense and sparse vectors when constructing PointStruct
- [x] 2.3 Add `search_sparse()` method — use Qdrant's `query_points()` for sparse vector search
- [x] 2.4 Add collection config detection method `has_sparse_vectors(collection_name)` — check if collection has sparse vectors configured

## 3. Hybrid Retrieval Implementation (HybridSearcher)

- [x] 3.1 Rewrite `search()` method — implement true hybrid retrieval using Qdrant `QueryRequest` with `prefetch` + `fusion=Models.Fusion.RRF`
- [x] 3.2 Add `mode` parameter support — `"hybrid"` (default) / `"dense"` / `"sparse"` three modes
- [x] 3.3 Implement fallback logic — when collection has no sparse vector config, auto-degrade to dense-only and log warning
- [x] 3.4 Update `HybridSearchResult` — add `sparse_score` field to record sparse retrieval score

## 4. Knowledge Base Service Update (KnowledgeBaseService)

- [x] 4.1 Update `ingest_document()` — call `embedding_service.encode()` in the pipeline to get sparse vectors, pass to `qdrant_indexer.upsert_vectors()`
- [x] 4.2 Update `search()` — add `mode: str = "hybrid"` parameter, pass mode to `hybrid_searcher.search()`
- [x] 4.3 Add `reindex_with_sparse(collection_name)` method — reindex existing collections, generate and store sparse vectors for existing documents

## 5. EnginePort Integration

- [x] 5.1 Update `AgentExecutionPort.knowledge_query()` — inject `KnowledgeBaseService`, look up Qdrant collection names for kb_ids, call `search()` to return results
- [x] 5.2 Add `kb_id → collection_name` mapping logic — query `KnowledgeBaseModel` to get the `qdrant_collection` field

## 6. Model & Configuration

- [x] 6.1 Update `KnowledgeBaseModel` — add `search_mode` field (default `"hybrid"`) and `sparse_weight` field (default `0.3`)
- [x] 6.2 Generate and execute Alembic migration script

## 7. Tests

- [x] 7.1 Write `test_embedding_sparse.py` — test sparse vector generation (encode/encode_query/mock)
- [x] 7.2 Write `test_hybrid_search.py` — test hybrid search (hybrid/dense/sparse modes, fallback)
- [x] 7.3 Write `test_knowledge_service.py` — test ingest with sparse vectors, search multi-mode
- [x] 7.4 Write `test_engine_port_knowledge.py` — test EnginePort.knowledge_query real invocation
- [x] 7.5 Full validation: `ruff check src/` + `mypy src/` + `pytest tests/ -q`
