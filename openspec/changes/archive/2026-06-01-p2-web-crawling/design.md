## Context

The RAG pipeline is modular: `parser → chunker → embedder → indexer`. The `DocumentParser` already supports HTML files via BeautifulSoup. The `KnowledgeBaseService.ingest_document()` orchestrates the full pipeline.

For web crawling, we need to:
1. Fetch URL content (HTML)
2. Extract clean text (reuse existing HTML parser)
3. Feed into existing pipeline

## Goals / Non-Goals

**Goals:**
- Crawl single URL and ingest into knowledge base
- Batch crawl multiple URLs
- Extract metadata (title, description) from HTML
- Show crawl status in frontend
- Respect robots.txt (basic)

**Non-Goals:**
- Deep crawling (following links recursively) — deferred
- JavaScript rendering (SPA sites) — deferred
- Rate limiting per domain — deferred
- Persistent crawl queue — deferred

## Decisions

### D1: New WebCrawler service (not extending parser)

**Decision**: Create `src/hecate/services/rag/crawler.py` as a new service that fetches URLs and returns extracted text + metadata.

**Rationale**: Separation of concerns. Parser handles local files, crawler handles URLs. Both feed into the same chunker/embedder/indexer pipeline.

### D2: Use httpx for HTTP requests

**Decision**: Use `httpx` (already in dependencies) for async HTTP requests.

**Rationale**: Already available, async-native, good timeout handling.

### D3: BeautifulSoup for HTML parsing

**Decision**: Reuse existing `DocumentParser._parse_html()` logic for text extraction.

**Rationale**: Already tested, handles common HTML patterns.

### D4: Store crawled content as documents

**Decision**: Create `DocumentModel` records for crawled URLs with `file_path` set to a virtual path like `web://{domain}/{path}`.

**Rationale**: Consistent with existing document management. Users can see crawled content alongside uploaded files.

## Risks / Trade-offs

- **[No JS rendering]** → SPA sites (React, Vue) won't render content. Mitigation: document this limitation.
- **[No robots.txt]** → May crawl restricted pages. Mitigation: add basic robots.txt check.
- **[Large pages]** → Some pages are very large. Mitigation: truncate to configurable max size.
