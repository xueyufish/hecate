## ADDED Requirements

### Requirement: VectorStore ABC defines backend-agnostic vector operations
The `VectorStore` abstract base class in `services/rag/vector_store.py` SHALL define the interface for all vector store backends: `create_collection`, `delete_collection`, `collection_exists`, `upsert`, `delete_by_ids`, `search_dense`, `search_sparse`, `count`, and `scroll`. All methods SHALL be async and use the shared `SearchResult` type from `services/rag/types.py`.

#### Scenario: ABC is not instantiable
- **WHEN** `VectorStore()` is called directly
- **THEN** it SHALL raise `TypeError` because abstract methods are not implemented

#### Scenario: Complete adapter implementation
- **WHEN** a subclass implements all abstract methods
- **THEN** it SHALL be instantiable without error

### Requirement: VectorStore provides optional hybrid search with application-layer RRF fallback
The `VectorStore` ABC SHALL define `search_hybrid()` as a non-abstract method with a default implementation that performs application-layer RRF fusion using `search_dense()` and `search_sparse()` with 4× prefetch factor and k=60.

#### Scenario: Default hybrid search (application-layer RRF)
- **WHEN** `search_hybrid(dense_query, sparse_query, top_k=10)` is called on a backend that does not override it
- **THEN** the default implementation SHALL call `search_dense(dense_query, top_k=40)` and `search_sparse(sparse_query, top_k=40)`, fuse via RRF with k=60, and return the top 10 results

#### Scenario: Native hybrid override
- **WHEN** a backend overrides `search_hybrid()` (e.g., QdrantVectorStore)
- **THEN** it SHALL use the backend's native hybrid query mechanism instead of the default RRF fusion

#### Scenario: supports_hybrid property
- **WHEN** `supports_hybrid` is checked on a backend
- **THEN** it SHALL return `True` if the backend overrides `search_hybrid()` with native implementation, or `False` if using the default application-layer fallback

### Requirement: VectorStore factory instantiates correct backend
A `get_vector_store()` factory function in `services/rag/factory.py` SHALL read `VECTOR_STORE_TYPE` from settings and return the corresponding `VectorStore` instance. Unknown types SHALL raise `ValueError`.

#### Scenario: Qdrant backend selection
- **WHEN** `VECTOR_STORE_TYPE=qdrant`
- **THEN** `get_vector_store()` SHALL return a `QdrantVectorStore` instance configured with `QDRANT_URL`

#### Scenario: Chroma backend selection
- **WHEN** `VECTOR_STORE_TYPE=chroma`
- **THEN** `get_vector_store()` SHALL return a `ChromaVectorStore` instance configured with `CHROMA_PERSIST_DIR`

#### Scenario: Unknown backend type
- **WHEN** `VECTOR_STORE_TYPE` is set to an unsupported value (e.g., "milvus")
- **THEN** `get_vector_store()` SHALL raise `ValueError` with a message listing supported types

### Requirement: QdrantVectorStore implements VectorStore for Qdrant
The `QdrantVectorStore` in `services/rag/qdrant_store.py` SHALL implement all `VectorStore` abstract methods by delegating to the Qdrant client. It SHALL override `search_hybrid()` to use Qdrant's native RRF fusion via `Prefetch + FusionQuery`. It SHALL support mock fallback when `qdrant-client` is not installed.

#### Scenario: Lazy client initialization
- **WHEN** `QdrantVectorStore` is instantiated
- **THEN** the Qdrant client SHALL NOT be created until the first operation (lazy loading)

#### Scenario: Mock fallback
- **WHEN** `qdrant-client` is not installed
- **THEN** all operations SHALL return deterministic mock results (matching current `QdrantIndexer` behavior)

#### Scenario: Native hybrid search
- **WHEN** `search_hybrid()` is called on `QdrantVectorStore`
- **THEN** it SHALL use Qdrant's `Prefetch` (dense + sparse) with `Fusion.RRF` in a single query

#### Scenario: supports_hybrid returns True
- **WHEN** `supports_hybrid` is checked on `QdrantVectorStore`
- **THEN** it SHALL return `True`

### Requirement: ChromaVectorStore implements VectorStore for Chroma
The `ChromaVectorStore` in `services/rag/chroma_store.py` SHALL implement all `VectorStore` abstract methods using the `chromadb` library. It SHALL NOT override `search_hybrid()`, inheriting the default application-layer RRF fallback. It SHALL support mock fallback when `chromadb` is not installed.

#### Scenario: Local persistence
- **WHEN** `ChromaVectorStore(persist_dir="./data/chroma")` is created
- **THEN** it SHALL use `chromadb.PersistentClient` with the given directory

#### Scenario: Dense vector search
- **WHEN** `search_dense(collection_name, query_vector, limit)` is called
- **THEN** it SHALL query Chroma's default collection using cosine similarity

#### Scenario: Sparse search returns empty
- **WHEN** `search_sparse(collection_name, query_sparse, limit)` is called
- **THEN** it SHALL return an empty list (Chroma does not support sparse/BM25 search) and log a warning

#### Scenario: Mock fallback
- **WHEN** `chromadb` is not installed
- **THEN** all operations SHALL return deterministic mock results

#### Scenario: supports_hybrid returns False
- **WHEN** `supports_hybrid` is checked on `ChromaVectorStore`
- **THEN** it SHALL return `False`

### Requirement: SearchResult type shared across backends
The `SearchResult` dataclass SHALL be defined in `services/rag/types.py` with fields `id: str`, `score: float`, and `payload: dict[str, Any]`. All `VectorStore` implementations SHALL return this type.

#### Scenario: SearchResult imported by ABC and adapters
- **WHEN** any `VectorStore` implementation returns search results
- **THEN** each result SHALL be a `SearchResult` instance with `id`, `score`, and `payload` fields
