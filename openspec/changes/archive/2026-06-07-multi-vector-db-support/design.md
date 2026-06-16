## Context

Hecate's RAG pipeline (`services/rag/`) is tightly coupled to Qdrant through `QdrantIndexer` — a monolithic class at `indexer.py` that serves as both the vector store interface and Qdrant client wrapper. It's instantiated as a module-level singleton (`qdrant_indexer = QdrantIndexer()`) and used directly by `HybridSearcher`, `KnowledgeBaseService`, and indirectly by `AgentExecutionPort`. The `KnowledgeBaseModel` has a `qdrant_collection` column that binds the data model to a specific backend. Config (`core/config.py`) hardcodes `QDRANT_URL`.

The five-layer architecture (AD-2) places RAG in the Capability Services layer — `services/rag/` depends on external libraries and infrastructure, which is the correct layer for a pluggable vector store abstraction.

## Goals / Non-Goals

**Goals:**
- Define a `VectorStore` ABC that decouples RAG operations from any specific vector database
- Migrate existing Qdrant implementation to `QdrantVectorStore` adapter (zero behavioral change)
- Add `ChromaVectorStore` as a lightweight development backend
- Support hybrid search transparently: backends with native hybrid (Qdrant, Milvus) use it; others fall back to application-layer RRF fusion
- Rename `qdrant_collection` → `collection_name` for backend-agnostic data model
- Introduce `VECTOR_STORE_TYPE` config pattern following Dify's proven approach

**Non-Goals:**
- Milvus and Weaviate adapters (P2/P3, future changes)
- Per-collection backend selection (global backend only)
- Moving `PostgresCheckpointStore` from `engine/` to `services/` (noted as D8, separate change)
- Changing the `EmbeddingService` interface or BGE-M3 model
- Modifying the `EnginePort.knowledge_query()` abstract interface

## Decisions

### D1: VectorStore ABC location — `services/rag/vector_store.py`

**Choice**: Place the ABC in `services/rag/`, NOT in `engine/`.

**Rationale**: VectorStore is a Capability Services concern (RAG), not an Execution Engine concept. The engine layer has zero external dependencies; vector store clients (qdrant-client, chromadb) are external libraries. This follows AD-2 Five-Layer Architecture: VectorStore sits at the Capability Services layer, same as `EmbeddingService` and `KnowledgeBaseService`.

**Alternatives considered**: `engine/ports.py` — rejected because `EnginePort` already defines `knowledge_query()` as an abstract delegation point, and vector store is an implementation detail behind that port, not an engine abstraction.

### D2: Hybrid search approach — "A+" optional native with app-layer fallback

**Choice**: ABC defines `search_dense()` + `search_sparse()` (required abstract) + `search_hybrid()` (optional with default implementation using application-layer RRF fusion with 4× prefetch).

```python
class VectorStore(ABC):
    @abstractmethod
    async def search_dense(self, ...) -> list[SearchResult]: ...
    
    @abstractmethod
    async def search_sparse(self, ...) -> list[SearchResult]: ...
    
    @property
    def supports_hybrid(self) -> bool:
        return False  # Override in backends with native hybrid
    
    async def search_hybrid(self, dense_query, sparse_query, ..., top_k: int) -> list[SearchResult]:
        # Default: application-layer RRF with 4× prefetch
        dense = await self.search_dense(dense_query, top_k=top_k * 4)
        sparse = await self.search_sparse(sparse_query, top_k=top_k * 4)
        return _rrf_fuse(dense, sparse, k=60, top_k=top_k)
```

**Rationale**: Research across 6 platforms (Dify, LlamaIndex, RAGFlow, Bisheng, Langflow, AgentScope) confirmed that all keep hybrid logic out of the ABC. Our A+ approach goes further by providing a working default — backends without native hybrid (Chroma) get automatic fallback, while Qdrant/Milvus override for zero quality loss. The 4× prefetch mitigates prefix sampling bias (a document ranked #15 in dense and #12 in sparse is invisible at K=10 but could be the fusion winner).

**Alternatives considered**:
- A (pure app-layer): Loses quality on backends that support native hybrid
- B (ABC defines hybrid as abstract): Forces all backends to implement hybrid, impractical for Chroma

### D3: RRF k constant — 60 (standard)

**Choice**: Use k=60 in the application-layer RRF, matching the Cormack et al. (2009) paper's empirical recommendation.

**Rationale**: Qdrant uses k=2 (non-standard, more aggressive rank differentiation). Using k=60 in our default fusion provides consistent, well-studied behavior. When backends override `search_hybrid()` with native fusion (like Qdrant), they use their own k internally — the ABC doesn't prescribe k for native paths.

### D4: Column rename — `qdrant_collection` → `collection_name`

**Choice**: Alembic migration to rename the column, update all references.

**Rationale**: Leaving `qdrant_` prefix violates the change's core goal of backend decoupling. Early project (minimal data), migration cost is negligible.

### D5: Config pattern — `VECTOR_STORE_TYPE` + per-backend env vars

**Choice**: Separate type selector and per-backend connection config, matching Dify's proven pattern:
```bash
VECTOR_STORE_TYPE=qdrant    # Global type selector
QDRANT_URL=http://...       # Qdrant-specific (when type=qdrant)
QDRANT_API_KEY=             # Optional
CHROMA_PERSIST_DIR=./data   # Chroma-specific (when type=chroma)
```

**Rationale**: Unlike `DATABASE_URL` where SQLAlchemy provides a universal connection format, vector databases have fundamentally different connection models (HTTP, gRPC, local file path). A single URL scheme would be unnatural for embedded backends like Chroma (`chroma:///path/to/data`). Dify (VECTOR_STORE) and RAGFlow (DOC_ENGINE) both use this pattern in production.

### D6: Adapter instantiation — Factory function

**Choice**: A `get_vector_store()` factory function in `services/rag/factory.py` that reads config and returns the correct `VectorStore` instance.

```python
def get_vector_store() -> VectorStore:
    match settings.vector_store_type:
        case "qdrant": return QdrantVectorStore(url=settings.qdrant_url, ...)
        case "chroma": return ChromaVectorStore(persist_dir=settings.chroma_persist_dir, ...)
        case _: raise ValueError(f"Unsupported VECTOR_STORE_TYPE: {settings.vector_store_type}")
```

**Rationale**: Simple match/case factory. No dependency injection framework needed — follows the same pattern as `QdrantIndexer`'s lazy init but with type-based selection. Replaces the module-level singleton with a function that returns the correct adapter.

### D7: SearchResult type — Shared dataclass in `services/rag/types.py`

**Choice**: Move `SearchResult` from `indexer.py` to `types.py` (which already exists for `Citation`). This becomes the shared return type for all vector store backends.

**Rationale**: Currently `SearchResult` is defined in `indexer.py`. With the ABC in a separate file, the return type must be importable by both the ABC and all adapters without circular dependencies.

## Risks / Trade-offs

**[Application-layer RRF quality gap]** → Mitigated by 4× prefetch. For K=10 final results, we fetch 40 from each channel. Empirical research shows this preserves >95% of fusion candidates. Enterprise deployments using Qdrant/Milvus get native fusion via override, zero gap.

**[Breaking API change: `qdrant_collection` → `collection_name`]** → Alembic migration handles DB. Pydantic schema field rename is a breaking API change for any external consumers. Mitigated by early project stage (limited external API usage). Document in changelog.

**[Chroma performance]** → Chroma is Python-only, no native ANN index. Intended for development/small datasets only. Production deployments use Qdrant/Milvus. The config documentation will clearly state this.

**[Mock fallback consistency]** → Current `QdrantIndexer` has mock fallback when `qdrant-client` is not installed. This pattern must be preserved in `QdrantVectorStore`. `ChromaVectorStore` similarly needs mock fallback for test environments. The factory must handle missing optional dependencies gracefully.

## Migration Plan

1. **Phase 1 — ABC + Qdrant adapter**: Create `VectorStore` ABC, refactor `QdrantIndexer` into `QdrantVectorStore`, update factory. All existing tests pass (no behavioral change).
2. **Phase 2 — Column rename**: Alembic migration `qdrant_collection` → `collection_name`. Update model, schemas, and all references.
3. **Phase 3 — Config update**: Add `VECTOR_STORE_TYPE` to Settings, update factory, update `.env.example`.
4. **Phase 4 — Chroma adapter**: New `ChromaVectorStore` with mock fallback. Tests for the adapter.
5. **Phase 5 — Update consumers**: Refactor `HybridSearcher`, `KnowledgeBaseService`, `AgentExecutionPort` to use factory instead of `qdrant_indexer` singleton.

**Rollback**: Each phase is independently committable. If Chroma adapter has issues, phases 1-4 are unaffected. The column rename migration has a corresponding downgrade.

## Open Questions

- None — all key decisions resolved during explore phase (D7-D12).
