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
The `QdrantIndexer` SHALL create collections, upsert vectors (dense+sparse), and perform search (dense, sparse, hybrid).

#### Scenario: Create collection with sparse config
- **WHEN** `create_collection(name, with_sparse=True)` is called
- **THEN** it SHALL create a Qdrant collection with COSINE dense vectors and sparse vector config

#### Scenario: Upsert with sparse vectors
- **WHEN** `upsert_vectors()` is called with sparse_vectors
- **THEN** each point SHALL have both dense and sparse vector representations

#### Scenario: Hybrid search with RRF fusion
- **WHEN** `search_hybrid()` is called
- **THEN** it SHALL use Qdrant prefetch (dense + sparse) with Fusion.RRF

#### Scenario: Mock fallback
- **WHEN** qdrant-client is not installed
- **THEN** all operations SHALL use mock implementations returning deterministic results

### Requirement: HybridSearcher fuses dense and sparse results
The `HybridSearcher` SHALL support hybrid, dense-only, and sparse-only search modes.

#### Scenario: Hybrid search with fallback
- **WHEN** sparse vectors are unavailable (no sparse embedding or collection lacks sparse config)
- **THEN** it SHALL fall back to dense-only search and log a warning

#### Scenario: Dense-only search
- **WHEN** `mode="dense"` is specified
- **THEN** it SHALL perform dense vector search only

#### Scenario: Score breakdown on hybrid results
- **WHEN** hybrid search is performed
- **THEN** each result SHALL have `dense_score` and `sparse_score` populated from parallel dense/sparse searches

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
The `KnowledgeBaseService` SHALL coordinate parse → chunk → embed → index and provide search capabilities.

#### Scenario: Document ingestion pipeline
- **WHEN** `ingest_document(file_path, collection_name)` is called
- **THEN** it SHALL parse → chunk → encode (dense+sparse) → upsert to Qdrant, returning `{"chunk_count": N, "collection": name}`

#### Scenario: Text ingestion (pre-extracted)
- **WHEN** `ingest_document_text(text, collection_name)` is called
- **THEN** it SHALL chunk → encode → upsert, skipping the parsing step

#### Scenario: Search with mode selection
- **WHEN** `search(collection_name, query, mode="hybrid")` is called
- **THEN** it SHALL delegate to `HybridSearcher.search()` with the specified mode

#### Scenario: Compare search modes
- **WHEN** `compare_modes(collection_name, query)` is called
- **THEN** it SHALL execute dense, sparse, and hybrid searches in parallel and return results per mode

#### Scenario: List chunks with pagination
- **WHEN** `list_chunks(collection_name, page, page_size)` is called
- **THEN** it SHALL use cursor-based scroll to return paginated chunk previews

#### Scenario: Re-index with sparse vectors
- **WHEN** `reindex_with_sparse(collection_name)` is called
- **THEN** it SHALL scroll through all points, generate sparse embeddings, and update them

### Requirement: Citation types for knowledge base retrieval
The `Citation` type SHALL represent a source document chunk with OpenAI-compatible annotation format.

#### Scenario: Citation to annotation
- **WHEN** `citation.to_annotation()` is called
- **THEN** it SHALL return `{"type": "kb_citation", "kb_citation": {...}}` with position, kb_id, document_name, chunk_id, score, and content_snippet
