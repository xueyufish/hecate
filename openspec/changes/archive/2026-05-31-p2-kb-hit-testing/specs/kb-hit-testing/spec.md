## ADDED Requirements

### Requirement: Knowledge base search endpoint for hit testing
The system SHALL provide a `POST /api/knowledge-bases/{id}/search` endpoint that accepts a JSON body with `query` (string, required), `mode` (string, optional, default `"hybrid"`, one of `"hybrid"` / `"dense"` / `"sparse"`), and `limit` (integer, optional, default 10, max 50), and returns a list of scored chunks matching the query.

Each result SHALL include: `id` (chunk ID), `score` (final relevance score), `content` (chunk text), `metadata` (source document info), `dense_score` (semantic similarity score), and `sparse_score` (keyword match score).

#### Scenario: Search with hybrid mode
- **WHEN** `POST /api/knowledge-bases/{kb_id}/search` with `{"query": "machine learning", "mode": "hybrid"}`
- **THEN** returns 200 with `{"results": [...], "query": "machine learning", "mode": "hybrid", "total": <count>}` where each result has `score`, `content`, `dense_score`, and `sparse_score`

#### Scenario: Search with nonexistent KB
- **WHEN** `POST /api/knowledge-bases/{nonexistent_id}/search`
- **THEN** returns 404 with error code `NOT_FOUND`

#### Scenario: Search with empty query
- **WHEN** `POST /api/knowledge-bases/{kb_id}/search` with `{"query": ""}`
- **THEN** returns 422 with validation error

### Requirement: Chunk browsing endpoint
The system SHALL provide a `GET /api/knowledge-bases/{id}/chunks` endpoint that returns a paginated list of stored chunks from the Qdrant collection, including `id`, `content` (truncated to 200 chars), and `metadata` for each chunk.

#### Scenario: Browse chunks with pagination
- **WHEN** `GET /api/knowledge-bases/{kb_id}/chunks?page=1&page_size=20`
- **THEN** returns 200 with `{"items": [...], "total": <count>}` where each item has `id`, `content_preview`, and `metadata`

#### Scenario: Browse chunks of empty KB
- **WHEN** `GET /api/knowledge-bases/{kb_id}/chunks` for a KB with no documents
- **THEN** returns 200 with `{"items": [], "total": 0}`

### Requirement: Search mode comparison endpoint
The system SHALL provide a `POST /api/knowledge-bases/{id}/compare` endpoint that runs the same query across dense, sparse, and hybrid modes, returning results for each mode in a single response.

#### Scenario: Compare modes for a query
- **WHEN** `POST /api/knowledge-bases/{kb_id}/compare` with `{"query": "API authentication"}`
- **THEN** returns 200 with `{"dense": {"results": [...]}, "sparse": {"results": [...]}, "hybrid": {"results": [...]}, "query": "API authentication"}` where each mode has up to 5 results

#### Scenario: Compare with custom limit
- **WHEN** `POST /api/knowledge-bases/{kb_id}/compare` with `{"query": "test", "limit": 3}`
- **THEN** each mode returns at most 3 results

## MODIFIED Requirements

### Requirement: HybridSearchResult includes per-mode score breakdown
The system SHALL include `dense_score` and `sparse_score` fields in `HybridSearchResult`, populated with the individual mode scores when hybrid search is performed, so users can understand each mode's contribution to the final ranking.

#### Scenario: Hybrid search returns both mode scores
- **WHEN** a hybrid search is executed for a query
- **THEN** each result includes `score` (fused), `dense_score` (semantic), and `sparse_score` (keyword) fields
