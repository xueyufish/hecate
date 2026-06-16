## ADDED Requirements

### Requirement: EmbeddingService generates dense and sparse vectors
The `EmbeddingService` SHALL use BGE-M3 (1024-dim dense + sparse) with lazy model loading and mock fallback.

#### Scenario: Lazy model loading
- **WHEN** FlagEmbedding is not installed
- **THEN** the service SHALL use mock embeddings (MD5-hash-based deterministic vectors)

#### Scenario: Encode batch
- **WHEN** `encode(texts)` is called with a list of texts
- **THEN** it SHALL return a list of `EmbeddingResult` with dense (1024-dim) and sparse (token_id→weight) vectors

#### Scenario: Encode single query
- **WHEN** `encode_query(query)` is called
- **THEN** it SHALL return a single `EmbeddingResult` (wrapper around `encode([query])`)

### Requirement: DocumentParser extracts text from multiple formats
The `DocumentParser` SHALL support PDF, DOCX, HTML, Markdown, TXT, CSV, JSON, XML, RST with lazy library loading.

#### Scenario: Supported file extension
- **WHEN** `parse(file_path)` is called with a supported extension
- **THEN** it SHALL return the extracted text content

#### Scenario: Unsupported format
- **WHEN** `parse(file_path)` is called with an unsupported extension
- **THEN** it SHALL raise ValueError("Unsupported file format: {ext}")

#### Scenario: File not found
- **WHEN** `parse(file_path)` is called with a non-existent file
- **THEN** it SHALL raise FileNotFoundError

#### Scenario: PDF parsing fallback chain
- **WHEN** pdfplumber is not installed
- **THEN** it SHALL fall back to PyPDF2; if neither is installed, raise ImportError

### Requirement: TextChunker splits text with overlap
The `TextChunker` SHALL split text into fixed-size chunks with overlap, preferring natural break points.

#### Scenario: Empty text
- **WHEN** `chunk_text("")` is called
- **THEN** it SHALL return an empty list

#### Scenario: Natural break preference
- **WHEN** a chunk boundary falls within the outer half of the chunk
- **THEN** it SHALL prefer breaking at the last period or newline

#### Scenario: Chunk metadata
- **WHEN** chunks are created
- **THEN** each `Chunk` SHALL have content, index, start_char, end_char, and copied metadata

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

### Requirement: MinIOStorage provides object storage with lazy client
The `MinIOStorage` SHALL upload, download, and delete files with lazy MinIO client loading and mock fallback.

#### Scenario: Upload file
- **WHEN** `upload_file(path, data, content_type)` is called
- **THEN** it SHALL upload to the configured bucket and return the file path

#### Scenario: Download file
- **WHEN** `download_file(path)` is called
- **THEN** it SHALL return the file content as bytes

#### Scenario: Auto-ensure bucket
- **WHEN** the MinIO client is initialized
- **THEN** it SHALL create the bucket if it doesn't exist

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

### Requirement: Citation types for knowledge base retrieval
The `Citation` type SHALL represent a source document chunk with OpenAI-compatible annotation format.

#### Scenario: Citation to annotation
- **WHEN** `citation.to_annotation()` is called
- **THEN** it SHALL return `{"type": "kb_citation", "kb_citation": {...}}` with position, kb_id, document_name, chunk_id, score, and content_snippet
