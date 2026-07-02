## Context

Hecate's RAG pipeline is complete with hybrid search (3.2.2 + 3.2.3). The service layer has:

- **`KnowledgeBaseService.search()`** — accepts collection_name, query, limit, mode; returns `list[HybridSearchResult]`
- **`HybridSearchResult`** — `id`, `score`, `content`, `metadata`, `sparse_score`
- **`HybridSearcher`** — supports `"hybrid"`, `"dense"`, `"sparse"` modes via Qdrant native fusion
- **`KnowledgeBaseModel`** — has `qdrant_collection` (maps kb_id → collection name), `search_mode`, `sparse_weight`
- **KB API** (`api/management/knowledge.py`) — CRUD + document upload, but **no search endpoint**

Chunks are stored in Qdrant (not PostgreSQL). Each point has a vector, optional sparse vector, and payload with `content`, `metadata` (including source document info). The QdrantIndexer has `search()`, `search_sparse()`, `search_hybrid()` methods.

## Goals / Non-Goals

**Goals:**
- Expose a search endpoint for KB hit testing: users send a query, get scored results with content snippets
- Provide chunk browsing: paginated list of stored chunks so users can verify ingestion quality
- Mode comparison: run same query across dense/sparse/hybrid and return side-by-side results
- Return actionable detail: score, content snippet, source document, dense/sparse score breakdown

**Non-Goals:**
- No new ORM models — chunks live in Qdrant, not PostgreSQL
- No chunk editing or deletion UI — that's KB management, not hit testing
- No automatic quality scoring or recommendations — users interpret results themselves
- No re-ranking or cross-encoder — that's P4 (3.2.5)

## Decisions

### D1: Search endpoint as POST (not GET)

**Decision**: `POST /api/knowledge-bases/{id}/search` with JSON body `{query, mode, limit}`.

**Rationale**: Search queries can be long (full sentences, multi-line). POST body is more natural than URL-encoded GET params. Consistent with the `POST /api/workflows/{id}/test-run` pattern in the codebase.

**Alternative considered**: `GET /api/knowledge-bases/{id}/search?q=...&mode=...` — rejected because queries often exceed URL length limits and contain special characters.

### D2: Chunk listing via Qdrant scroll, not PostgreSQL

**Decision**: `GET /api/knowledge-bases/{id}/chunks` uses Qdrant's `scroll()` API to paginate through stored points. No chunk data in PostgreSQL.

**Rationale**: Chunks are stored exclusively in Qdrant payloads. Adding a PostgreSQL mirror would be redundant and introduce sync issues. Qdrant's `scroll()` supports cursor-based pagination natively.

**Alternative considered**: Store chunk content in a `DocumentChunkModel` — rejected because it duplicates Qdrant data and requires sync logic.

### D3: Mode comparison as a separate endpoint

**Decision**: `POST /api/knowledge-bases/{id}/compare` runs the query in all 3 modes (dense, sparse, hybrid) and returns a structured response with results per mode.

**Rationale**: Comparison is a distinct use case from single-mode search. Users want to see how different modes rank the same query. A separate endpoint keeps the search endpoint simple and the comparison endpoint focused.

**Alternative considered**: Add `compare=true` flag to the search endpoint — rejected because the response shape differs significantly (3 result sets vs 1).

### D4: Score breakdown via HybridSearchResult extension

**Decision**: Extend `HybridSearchResult` with optional `dense_score` and `sparse_weight` fields (not just `sparse_score`). When hybrid mode is used, both individual scores are populated so users can see each mode's contribution.

**Rationale**: The current `sparse_score` field only shows the sparse contribution. For hit testing transparency, users need to see both dense and sparse scores independently, plus the final fused score.

## Risks / Trade-offs

- **[Qdrant scroll performance]** — Scrolling through large collections (100k+ chunks) may be slow. → Mitigation: enforce page_size limits (max 50), use cursor-based pagination.
- **[Search latency for comparison]** — Compare endpoint runs 3 searches sequentially. → Mitigation: run in parallel with `asyncio.gather()`, each with a reduced limit (default 5).
- **[No chunk count in PostgreSQL]** — Users can't see total chunk count without querying Qdrant. → Mitigation: use Qdrant's `count()` API to get collection size.
