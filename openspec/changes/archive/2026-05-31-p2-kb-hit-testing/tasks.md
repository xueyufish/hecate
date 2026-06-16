## 1. Searcher Score Breakdown

- [x] 1.1 Update `HybridSearchResult` in `searcher.py` — add `dense_score: float = 0.0` field alongside existing `sparse_score`
- [x] 1.2 Update `HybridSearcher.search()` — when mode is `"hybrid"`, populate both `dense_score` and `sparse_score` on each result by running dense and sparse searches separately before fusion (or extracting from Qdrant prefetch results)
- [x] 1.3 Update existing tests in `test_hybrid_search.py` to verify `dense_score` and `sparse_score` are populated in hybrid mode results

## 2. Service Layer — KB Search & Chunks

- [x] 2.1 Add `search_kb()` method to `KnowledgeBaseService` — accepts kb_id (UUID), query (str), mode (str), limit (int); looks up `KnowledgeBaseModel.qdrant_collection`, calls `self.search()` with the collection name, returns scored results with score breakdown
- [x] 2.2 Add `list_chunks()` method to `KnowledgeBaseService` — accepts kb_id, page, page_size; uses Qdrant `scroll()` API to paginate through collection points; returns chunk list with id, content preview (truncated 200 chars), metadata
- [x] 2.3 Add `compare_modes()` method to `KnowledgeBaseService` — accepts kb_id, query, limit; runs search in dense, sparse, and hybrid modes via `asyncio.gather()`; returns dict with results per mode
- [x] 2.4 Add `get_chunk_count()` helper — uses Qdrant `count()` API to return total points in a collection

## 3. API Layer — Hit Testing Endpoints

- [x] 3.1 Add `POST /api/knowledge-bases/{id}/search` endpoint in `knowledge.py` — accepts `SearchRequest(query, mode, limit)`, calls `KnowledgeBaseService.search_kb()`, returns scored results with score breakdown
- [x] 3.2 Add `GET /api/knowledge-bases/{id}/chunks` endpoint — accepts page/page_size params, calls `KnowledgeBaseService.list_chunks()`, returns paginated chunk list
- [x] 3.3 Add `POST /api/knowledge-bases/{id}/compare` endpoint — accepts `CompareRequest(query, limit)`, calls `KnowledgeBaseService.compare_modes()`, returns side-by-side results for dense/sparse/hybrid
- [x] 3.4 Add Pydantic request/response schemas: `KBSearchRequest`, `KBSearchResultSchema`, `KBChunkSchema`, `KBCompareRequest`, `KBCompareResponse`

## 4. Tests

- [x] 4.1 Write `tests/test_api/test_kb_hit_testing.py` — test search endpoint (valid query, 404 for missing KB, 422 for empty query), chunks endpoint (paginated, empty KB), compare endpoint (returns all 3 modes)
- [x] 4.2 Write `tests/test_services/test_rag/test_kb_search_service.py` — test `search_kb()` with mock Qdrant, `list_chunks()` pagination, `compare_modes()` returns all modes
- [x] 4.3 Full validation: `ruff check src/hecate/ tests/` + `mypy src/` + `pytest tests/ -q`
