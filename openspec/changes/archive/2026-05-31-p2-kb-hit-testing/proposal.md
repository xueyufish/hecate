## Why

Users create knowledge bases, upload documents, and configure Agents with RAG — but have no way to verify retrieval quality before deploying. They cannot see what chunks are stored, whether their queries return relevant results, or how different search modes (dense/sparse/hybrid) affect ranking. This creates a trust gap: RAG either works or it doesn't, and users can't debug it.

The hybrid search infrastructure (3.2.2 + 3.2.3) is complete — `KnowledgeBaseService.search()` supports hybrid/dense/sparse modes and returns scored results with `HybridSearchResult`. But this capability is not exposed to users. A hit testing interface lets users validate their KB before connecting it to an Agent.

## What Changes

- Add a search/test endpoint to the KB API: `POST /api/knowledge-bases/{id}/search` accepting a query and optional mode/limit params, returning scored chunks with metadata
- Add a preview endpoint to inspect stored chunks: `GET /api/knowledge-bases/{id}/chunks` returning paginated chunk list with content preview
- Add search mode comparison: run the same query across dense/sparse/hybrid modes and return side-by-side results so users can compare ranking quality
- Return chunk-level detail including content snippet, score, source document, and search mode for each hit

## Capabilities

### New Capabilities

- `kb-hit-testing`: Knowledge base hit testing — search endpoint with scored results, chunk preview, mode comparison, and quality metrics

### Modified Capabilities

- `hybrid-search`: Add per-result score breakdown (dense_score, sparse_score) to HybridSearchResult for transparency in hit testing output

## Impact

- **API**: `src/hecate/api/management/knowledge.py` — 3 new endpoints (search, chunks, compare)
- **Services**: `src/hecate/services/rag/service.py` — expose existing search + add chunk listing
- **Searcher**: `src/hecate/services/rag/searcher.py` — return score breakdown per mode
- **Models**: No new ORM models needed — chunks are stored in Qdrant, not PostgreSQL
- **Dependencies**: No new external dependencies
