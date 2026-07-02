## Why

The RAG pipeline is tightly coupled to Qdrant — `QdrantIndexer` is a monolithic class used as a module-level singleton, the `KnowledgeBaseModel` has a `qdrant_collection` column, and config hardcodes `QDRANT_URL`. This prevents supporting alternative vector databases (Chroma for lightweight dev, Milvus/Weaviate for enterprise), violating the platform's model-agnostic and infrastructure-portable design principles.

## What Changes

- **New `VectorStore` ABC** in `services/rag/vector_store.py` defining a backend-agnostic interface: `create_collection`, `delete_collection`, `collection_exists`, `upsert`, `delete_by_ids`, `search_dense`, `search_sparse`, `count`, `scroll`, plus optional `search_hybrid` with default application-layer RRF fusion (4× prefetch to mitigate prefix sampling bias)
- **Refactor `QdrantIndexer`** into `QdrantVectorStore` implementing the ABC — extract existing Qdrant logic without behavioral changes
- **New `ChromaVectorStore`** as a lightweight, zero-dependency backend for development environments
- **VectorStore factory** that instantiates the correct backend based on `VECTOR_STORE_TYPE` config
- **`HybridSearcher` refactored** to call `VectorStore.search_hybrid()` instead of Qdrant-native fusion — backends with native hybrid (Qdrant, Milvus) override the method; backends without (Chroma) inherit the default application-layer RRF fallback
- **BREAKING: `qdrant_collection` column renamed to `collection_name`** in `KnowledgeBaseModel` via Alembic migration
- **Config change**: `QDRANT_URL` replaced by `VECTOR_STORE_TYPE` (global type selector) + per-backend env vars (`QDRANT_URL`, `CHROMA_PERSIST_DIR`, etc.), following Dify's proven pattern

## Capabilities

### New Capabilities
- `vector-store-abc`: Abstract base class defining the vector store interface with required ops (dense/sparse search, collection CRUD, upsert/delete) and optional hybrid search with application-layer RRF fallback

### Modified Capabilities
- `rag-pipeline`: `QdrantIndexer` replaced by `VectorStore` ABC + factory; `KnowledgeBaseService` uses the abstraction instead of direct Qdrant calls; `reindex_with_sparse()` encapsulation fixed
- `hybrid-search`: `HybridSearcher` delegates to `VectorStore.search_hybrid()` instead of Qdrant-native fusion; supports both native and fallback fusion transparently
- `core-infrastructure`: New `VECTOR_STORE_TYPE` config field and per-backend connection settings replace single `QDRANT_URL`
- `data-models`: `qdrant_collection` column renamed to `collection_name` via Alembic migration; Pydantic schemas updated

## Impact

- **Code**: `services/rag/indexer.py` (major refactor → split into ABC + Qdrant adapter), `services/rag/searcher.py` (refactor), `services/rag/service.py` (refactor to use factory), `models/knowledge.py` (column rename), `core/config.py` (new config fields), `services/orchestration/agent_execution_port.py` (adapt to new interface)
- **Database**: Alembic migration for `qdrant_collection` → `collection_name` column rename
- **API**: Pydantic schema field rename (`qdrant_collection` → `collection_name` in Create/Read schemas)
- **Dependencies**: `chromadb` added to `[rag]` optional dependency group
- **Configuration**: Users must set `VECTOR_STORE_TYPE=qdrant` (or `chroma`) and corresponding backend-specific env vars; `QDRANT_URL` remains valid when type is qdrant
- **Tests**: Engine tests unaffected (no direct Qdrant dependency); RAG service tests updated to test against ABC; new tests for `ChromaVectorStore` and factory
