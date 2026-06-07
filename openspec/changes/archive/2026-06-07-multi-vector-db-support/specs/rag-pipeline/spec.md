## MODIFIED Requirements

### Requirement: QdrantIndexer manages collections with dense+sparse vectors
The `QdrantIndexer` class SHALL be replaced by the `VectorStore` ABC and `QdrantVectorStore` adapter. All direct references to `qdrant_indexer` singleton SHALL be replaced by calls to `get_vector_store()` factory. The adapter SHALL maintain identical behavior to the previous `QdrantIndexer` for all operations: collection creation with dense/sparse config, vector upsert, dense/sparse/hybrid search, scroll, count, and sparse vector detection.

#### Scenario: Create collection with sparse config
- **WHEN** `create_collection(name, with_sparse=True)` is called via any VectorStore adapter
- **THEN** the underlying backend SHALL create a collection with dense vector config and sparse vector config if supported

#### Scenario: Upsert with sparse vectors
- **WHEN** `upsert()` is called with sparse vectors
- **THEN** each point SHALL have both dense and sparse vector representations (if the backend supports sparse)

#### Scenario: Hybrid search with RRF fusion
- **WHEN** `search_hybrid()` is called
- **THEN** backends with native hybrid (Qdrant) SHALL use server-side RRF; backends without (Chroma) SHALL use application-layer RRF with 4× prefetch and k=60

#### Scenario: Mock fallback
- **WHEN** the backend client library is not installed
- **THEN** all operations SHALL use mock implementations returning deterministic results

### Requirement: HybridSearcher fuses dense and sparse results
The `HybridSearcher` SHALL use `VectorStore.search_hybrid()` instead of calling `qdrant_indexer` directly. It SHALL accept a `VectorStore` instance (injected via constructor) and delegate all search operations to it. The searcher SHALL no longer import or reference `qdrant_indexer`.

#### Scenario: Hybrid search via VectorStore
- **WHEN** `HybridSearcher.search(collection_name, query, limit, mode="hybrid")` is called
- **THEN** it SHALL call `vector_store.search_hybrid()` which transparently uses native or fallback fusion

#### Scenario: Dense-only search via VectorStore
- **WHEN** `mode="dense"` is specified
- **THEN** it SHALL call `vector_store.search_dense()` only

#### Scenario: Sparse-only search via VectorStore
- **WHEN** `mode="sparse"` is specified
- **THEN** it SHALL call `vector_store.search_sparse()` only

#### Scenario: Score breakdown on hybrid results
- **WHEN** hybrid search is performed
- **THEN** the searcher SHALL run parallel dense and sparse searches alongside hybrid to populate `dense_score` and `sparse_score` on each result

#### Scenario: Hybrid search with fallback when sparse unavailable
- **WHEN** sparse vectors are unavailable (no sparse embedding or backend returns empty for sparse)
- **THEN** it SHALL fall back to dense-only search and log a warning

### Requirement: KnowledgeBaseService orchestrates the full RAG pipeline
The `KnowledgeBaseService` SHALL use `get_vector_store()` factory instead of the `qdrant_indexer` singleton. The `reindex_with_sparse()` method SHALL NOT access the vector store's private client directly — it SHALL use public `VectorStore` methods (`scroll`, `upsert`).

#### Scenario: Document ingestion pipeline
- **WHEN** `ingest_document(file_path, collection_name)` is called
- **THEN** it SHALL parse → chunk → encode (dense+sparse) → `vector_store.upsert()`, returning `{"chunk_count": N, "collection": name}`

#### Scenario: Text ingestion (pre-extracted)
- **WHEN** `ingest_document_text(text, collection_name)` is called
- **THEN** it SHALL chunk → encode → `vector_store.upsert()`, skipping the parsing step

#### Scenario: Search with mode selection
- **WHEN** `search(collection_name, query, mode="hybrid")` is called
- **THEN** it SHALL delegate to `HybridSearcher.search()` which uses `VectorStore.search_hybrid()`

#### Scenario: Re-index with sparse vectors encapsulation
- **WHEN** `reindex_with_sparse(collection_name)` is called
- **THEN** it SHALL use `vector_store.scroll()` to iterate points and `vector_store.upsert()` to update, without accessing any private client attributes

#### Scenario: List chunks with pagination
- **WHEN** `list_chunks(collection_name, page, page_size)` is called
- **THEN** it SHALL use `vector_store.scroll()` for cursor-based pagination
