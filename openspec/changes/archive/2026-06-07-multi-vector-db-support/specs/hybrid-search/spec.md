## MODIFIED Requirements

### Requirement: Hybrid search combines dense and sparse retrieval
The system SHALL perform hybrid search by calling `VectorStore.search_hybrid()` which transparently delegates to the backend's native hybrid (for backends like Qdrant that support it) or falls back to application-layer RRF fusion (for backends like Chroma that do not). The `HybridSearcher` SHALL no longer reference `qdrant_indexer` directly.

#### Scenario: Hybrid search with native backend (Qdrant)
- **WHEN** `HybridSearcher.search(collection_name, query, limit, mode="hybrid")` is called with a QdrantVectorStore
- **THEN** the system SHALL use Qdrant's native `Prefetch + Fusion.RRF` via the overridden `search_hybrid()` method

#### Scenario: Hybrid search with fallback backend (Chroma)
- **WHEN** `HybridSearcher.search(collection_name, query, limit, mode="hybrid")` is called with a ChromaVectorStore
- **THEN** the system SHALL use application-layer RRF fusion (4× prefetch, k=60) via the default `search_hybrid()` method

#### Scenario: Hybrid search with custom weights
- **WHEN** `HybridSearcher.search()` is called with custom `dense_weight` and `sparse_weight`
- **THEN** the weights SHALL be applied during score combination, influencing the final ranking

#### Scenario: Fallback when sparse vectors unavailable
- **WHEN** the target collection has no sparse vector configuration or the backend does not support sparse search
- **THEN** the system SHALL fall back to dense-only search and log a warning

### Requirement: Knowledge base service exposes hybrid search
The `KnowledgeBaseService.search()` method SHALL accept a `mode` parameter to control retrieval strategy: "hybrid" (default), "dense" (vector only), "sparse" (keyword only). All modes SHALL delegate through `VectorStore` methods, not directly to any specific backend.

#### Scenario: Search with hybrid mode
- **WHEN** `KnowledgeBaseService.search(collection_name, query, limit, mode="hybrid")` is called
- **THEN** it SHALL delegate to `HybridSearcher.search()` which calls `VectorStore.search_hybrid()`

#### Scenario: Search with dense-only mode
- **WHEN** `KnowledgeBaseService.search(collection_name, query, limit, mode="dense")` is called
- **THEN** it SHALL delegate to `HybridSearcher.search()` which calls `VectorStore.search_dense()`

#### Scenario: Search with sparse-only mode
- **WHEN** `KnowledgeBaseService.search(collection_name, query, limit, mode="sparse")` is called
- **THEN** it SHALL delegate to `HybridSearcher.search()` which calls `VectorStore.search_sparse()`

### Requirement: EnginePort knowledge_query wired to RAG services
The `AgentExecutionPort.knowledge_query()` SHALL delegate to `KnowledgeBaseService.search()`, using `collection_name` (not `qdrant_collection`) to look up the vector store collection for each knowledge base.

#### Scenario: Engine queries knowledge base
- **WHEN** the execution engine calls `knowledge_query(query, kb_ids)` on the port
- **THEN** it SHALL look up `collection_name` for the given `kb_ids` and call `KnowledgeBaseService.search()` for each, aggregating results

#### Scenario: Knowledge base not found
- **WHEN** `knowledge_query` is called with a `kb_id` that doesn't exist
- **THEN** it SHALL return an empty list and log a warning (not raise)
