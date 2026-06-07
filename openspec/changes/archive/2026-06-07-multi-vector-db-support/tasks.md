## 1. Shared Types & ABC Foundation

- [x] 1.1 Move `SearchResult` dataclass from `services/rag/indexer.py` to `services/rag/types.py` (update all imports)
- [x] 1.2 Create `services/rag/vector_store.py` with `VectorStore` ABC: abstract methods `create_collection`, `delete_collection`, `collection_exists`, `upsert`, `delete_by_ids`, `search_dense`, `search_sparse`, `count`, `scroll`; non-abstract `search_hybrid` with default RRF fusion (4× prefetch, k=60); property `supports_hybrid` defaulting to `False`
- [x] 1.3 Add private `_rrf_fuse()` helper function in `vector_store.py` implementing standard RRF: `score(d) = Σ 1/(k + rank_i(d))` with k=60, 1-based ranking

## 2. Qdrant Adapter

- [x] 2.1 Create `services/rag/qdrant_store.py` with `QdrantVectorStore(VectorStore)` — migrate all logic from `QdrantIndexer` methods to corresponding ABC methods, preserving mock fallback and lazy client init
- [x] 2.2 Override `search_hybrid()` in `QdrantVectorStore` to use Qdrant native `Prefetch + Fusion.RRF`; set `supports_hybrid = True`
- [ ] 2.3 Verify `QdrantVectorStore` passes all existing tests that previously tested `QdrantIndexer` behavior

## 3. Factory & Config

- [x] 3.1 Add to `core/config.py` Settings: `VECTOR_STORE_TYPE: str = "qdrant"`, `CHROMA_PERSIST_DIR: str = "./data/chroma"`; keep existing `QDRANT_URL`
- [x] 3.2 Create `services/rag/factory.py` with `get_vector_store() -> VectorStore` using match/case on `settings.vector_store_type`
- [x] 3.3 Update `.env.example` with `VECTOR_STORE_TYPE`, `CHROMA_PERSIST_DIR` entries

## 4. Column Rename (Breaking Change)

- [x] 4.1 Generate Alembic migration: rename `qdrant_collection` → `collection_name` on `knowledge_bases` table (include upgrade and downgrade)
- [x] 4.2 Update `models/knowledge.py`: rename `qdrant_collection` attribute to `collection_name`, update `mapped_column("collection_name", String(255))`
- [x] 4.3 Update Pydantic schemas: rename `qdrant_collection` → `collection_name` in CreateSchema and ReadSchema
- [x] 4.4 Update `services/orchestration/agent_execution_port.py`: change `kb.qdrant_collection` → `kb.collection_name` reference

## 5. Consumer Refactoring

- [x] 5.1 Refactor `services/rag/searcher.py`: `HybridSearcher` accepts `VectorStore` via constructor injection; replace all `qdrant_indexer` calls with `self._store` calls; remove `from indexer import qdrant_indexer`
- [x] 5.2 Refactor `services/rag/service.py`: `KnowledgeBaseService` uses `get_vector_store()` factory; replace `qdrant_indexer` with factory call; fix `reindex_with_sparse()` to use `scroll()` + `upsert()` instead of private client access
- [x] 5.3 Remove module-level singletons: delete `qdrant_indexer = QdrantIndexer()` from `indexer.py` and `hybrid_searcher = HybridSearcher()` from `searcher.py`; update all import sites
- [x] 5.4 Delete or deprecate `services/rag/indexer.py` (old `QdrantIndexer` class) once all consumers are migrated

## 6. Chroma Adapter

- [x] 6.1 Add `chromadb` to `[rag]` optional dependency group in `pyproject.toml`
- [x] 6.2 Create `services/rag/chroma_store.py` with `ChromaVectorStore(VectorStore)` — implement all abstract methods using `chromadb.PersistentClient`; `search_sparse()` returns empty list with warning (Chroma has no BM25); mock fallback when `chromadb` not installed
- [x] 6.3 Do NOT override `search_hybrid()` — inherit default application-layer RRF; `supports_hybrid` returns `False`

## 7. Tests

- [x] 7.1 Test `VectorStore` ABC: verify not instantiable directly; verify complete subclass works
- [x] 7.2 Test `QdrantVectorStore`: collection CRUD, upsert, search_dense, search_sparse, search_hybrid (native), scroll, count, mock fallback
- [x] 7.3 Test `ChromaVectorStore`: collection CRUD, upsert, search_dense, search_sparse (returns empty), scroll, count, mock fallback
- [x] 7.4 Test `_rrf_fuse()`: verify RRF formula with k=60, 1-based ranking, correct ordering, deduplication across channels
- [x] 7.5 Test `get_vector_store()` factory: returns correct type for "qdrant"/"chroma", raises ValueError for unknown types
- [x] 7.6 Test `HybridSearcher` with mocked VectorStore: hybrid mode calls `search_hybrid`, dense calls `search_dense`, sparse calls `search_sparse`, fallback when sparse unavailable
- [x] 7.7 Test `KnowledgeBaseService` with mocked VectorStore: ingest delegates correctly, search delegates to HybridSearcher, reindex uses scroll+upsert without private access
- [x] 7.8 Update existing RAG tests that reference `qdrant_indexer` to use `get_vector_store()` or mocked VectorStore

## 8. Verification

- [x] 8.1 Run `ruff check src/hecate/ tests/` — zero errors
- [x] 8.2 Run `ruff format --check src/ tests/` — zero errors
- [x] 8.3 Run `mypy src/` — zero errors
- [x] 8.4 Run `python -m pytest tests/ -q` — all tests pass
