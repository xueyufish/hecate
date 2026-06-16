## ADDED Requirements

### Requirement: Sparse vector generation from text
The system SHALL generate sparse vectors (token_id → weight mapping) from text input using BGE-M3's sparse encoding capability, alongside the existing dense vector generation.

#### Scenario: Encode text produces both dense and sparse vectors
- **WHEN** `EmbeddingService.encode(texts)` is called with a list of texts
- **THEN** each returned `EmbeddingResult` SHALL have a non-empty `sparse` field containing a `dict[int, float]` mapping token IDs to weights

#### Scenario: Encode query produces both dense and sparse vectors
- **WHEN** `EmbeddingService.encode_query(query)` is called with a single query string
- **THEN** the returned `EmbeddingResult` SHALL have both `dense` (1024-dim) and `sparse` (dict) fields populated

#### Scenario: Mock embedding generates deterministic sparse vectors
- **WHEN** the embedding model is not available (mock mode)
- **THEN** `EmbeddingResult.sparse` SHALL contain a deterministic mock sparse vector for testing

### Requirement: Qdrant collection supports sparse vectors
The system SHALL create Qdrant collections with both dense and sparse vector configurations, enabling hybrid search on indexed documents.

#### Scenario: Create collection with sparse vector config
- **WHEN** `QdrantIndexer.create_collection(collection_name, vector_size)` is called
- **THEN** the collection SHALL be created with both `vectors_config` (dense) and `sparse_vectors_config` (sparse named "sparse")

#### Scenario: Existing collection without sparse config
- **WHEN** a collection already exists without sparse vector configuration
- **THEN** the indexer SHALL detect the missing config and log a warning suggesting re-indexing

### Requirement: Sparse vector indexing
The system SHALL store sparse vectors alongside dense vectors when indexing documents, enabling later retrieval via sparse similarity search.

#### Scenario: Upsert with sparse vectors
- **WHEN** `QdrantIndexer.upsert_vectors()` is called with both dense and sparse vectors
- **THEN** each point SHALL be stored with both vector types in the Qdrant collection

#### Scenario: Upsert without sparse vectors (backward compatible)
- **WHEN** `QdrantIndexer.upsert_vectors()` is called with only dense vectors (sparse=None)
- **THEN** the point SHALL be stored with dense vector only, maintaining backward compatibility
