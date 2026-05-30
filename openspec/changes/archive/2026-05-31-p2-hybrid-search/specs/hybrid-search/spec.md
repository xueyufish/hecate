## ADDED Requirements

### Requirement: Hybrid search combines dense and sparse retrieval
The system SHALL perform hybrid search by executing both dense (semantic) and sparse (keyword) retrieval, then fusing the results using Qdrant's native QueryRequest fusion API.

#### Scenario: Hybrid search with default weights
- **WHEN** `HybridSearcher.search(collection_name, query, limit)` is called
- **THEN** the system SHALL execute a Qdrant QueryRequest with both dense and sparse prefetch, fusion=RRF, and return results ordered by fused score

#### Scenario: Hybrid search with custom weights
- **WHEN** `HybridSearcher.search()` is called with custom `dense_weight` and `sparse_weight`
- **THEN** the fusion SHALL apply the specified weights to influence the final ranking

#### Scenario: Fallback when sparse vectors unavailable
- **WHEN** the target collection has no sparse vector configuration
- **THEN** the system SHALL fall back to dense-only search and log a warning

### Requirement: Knowledge base service exposes hybrid search
The `KnowledgeBaseService.search()` method SHALL accept a `mode` parameter to control retrieval strategy: "hybrid" (default), "dense" (vector only), "sparse" (keyword only).

#### Scenario: Search with hybrid mode
- **WHEN** `KnowledgeBaseService.search(collection_name, query, limit, mode="hybrid")` is called
- **THEN** it SHALL delegate to `HybridSearcher.search()` which uses both dense and sparse retrieval

#### Scenario: Search with dense-only mode
- **WHEN** `KnowledgeBaseService.search(collection_name, query, limit, mode="dense")` is called
- **THEN** it SHALL perform dense vector search only, ignoring sparse vectors

#### Scenario: Search with sparse-only mode
- **WHEN** `KnowledgeBaseService.search(collection_name, query, limit, mode="sparse")` is called
- **THEN** it SHALL perform sparse vector search only (keyword/BM25 style)

### Requirement: EnginePort knowledge_query wired to RAG services
The `AgentExecutionPort.knowledge_query()` SHALL delegate to `KnowledgeBaseService.search()`, enabling the execution engine to retrieve knowledge during agent execution.

#### Scenario: Engine queries knowledge base
- **WHEN** the execution engine calls `knowledge_query(query, kb_ids)` on the port
- **THEN** it SHALL look up the Qdrant collection names for the given `kb_ids` and call `KnowledgeBaseService.search()` for each, aggregating results

#### Scenario: Knowledge base not found
- **WHEN** `knowledge_query` is called with a `kb_id` that doesn't exist
- **THEN** it SHALL return an empty list and log a warning (not raise)

### Requirement: Document ingestion stores sparse vectors
The `KnowledgeBaseService.ingest_document()` pipeline SHALL generate and store both dense and sparse vectors for each document chunk.

#### Scenario: Ingest with hybrid indexing
- **WHEN** `KnowledgeBaseService.ingest_document(file_path, collection_name)` is called
- **THEN** the pipeline SHALL generate sparse embeddings via `EmbeddingService.encode()` and pass both dense and sparse vectors to `QdrantIndexer.upsert_vectors()`

#### Scenario: Ingest with dense-only (fallback)
- **WHEN** sparse embedding generation fails (model not available)
- **THEN** the pipeline SHALL fall back to dense-only indexing and log a warning
