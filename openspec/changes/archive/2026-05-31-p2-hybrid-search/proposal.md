## Why

Hecate's RAG retrieval currently only supports dense vector search (3.2.1), relying on embedding model semantic similarity. This causes two problems:

1. **Missing exact keyword matching** — when users search for proper nouns, error codes, API names, etc., semantic retrieval may miss exact matches
2. **No hybrid retrieval** — unable to combine the strengths of semantic understanding and keyword matching, capping retrieval quality

Qdrant 1.7+ natively supports sparse vectors with a built-in fusion API. We already have `qdrant-client>=1.12.0`, so no additional dependencies are needed.

## What Changes

- Add BM25-style keyword retrieval (sparse vector indexing + search)
- Add hybrid retrieval (dense + sparse score fusion with configurable weights)
- Upgrade `HybridSearcher` from "dense-only" to true hybrid retrieval
- Change `EnginePort.knowledge_query` from `raise NotImplementedError` to a real RAG service call
- Add hybrid retrieval configuration fields to the knowledge base model (weights, strategy, etc.)

## Capabilities

### New Capabilities

- `keyword-search`: BM25-style sparse vector retrieval — includes sparse vector generation, Qdrant sparse vector collection management, sparse vector indexing and search
- `hybrid-search`: Dense + sparse hybrid retrieval — includes score fusion strategies (RRF / weighted), retrieval config management, EnginePort integration

### Modified Capabilities

- `context-assembler`: Knowledge retrieval results need to be injected into the context assembly flow (via the `EnginePort.knowledge_query` interface)

## Impact

**Code changes:**
- `src/hecate/services/rag/embedding.py` — Add sparse vector generation (BGE-M3 sparse output)
- `src/hecate/services/rag/indexer.py` — Qdrant collection creation supports sparse vector config
- `src/hecate/services/rag/searcher.py` — HybridSearcher implements real hybrid retrieval logic
- `src/hecate/services/rag/service.py` — KnowledgeBaseService.search supports hybrid mode parameter
- `src/hecate/engine/ports.py` — knowledge_query goes from NotImplementedError to real implementation
- `src/hecate/models/knowledge.py` — KnowledgeBaseModel adds hybrid retrieval config fields
- `pyproject.toml` — Optionally add `rank-bm25` dependency (fallback; primary approach uses Qdrant native)

**API changes:**
- `POST /api/knowledge-bases/{id}/search` — New search endpoint (optional, exposed as needed)

**Dependencies:**
- No new required dependencies (Qdrant native approach)
- Optional: `rank-bm25` as a fallback BM25 implementation
