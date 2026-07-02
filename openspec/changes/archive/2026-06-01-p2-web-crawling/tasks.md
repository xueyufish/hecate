## 1. Backend: WebCrawler Service

- [x] 1.1 Create `src/hecate/services/rag/crawler.py` with `WebCrawler` class
- [x] 1.2 Implement `crawl_url(url)` method that fetches URL content using httpx with timeout
- [x] 1.3 Implement HTML text extraction using BeautifulSoup (reuse parser logic)
- [x] 1.4 Extract metadata: title from `<title>`, description from `<meta name="description">`
- [x] 1.5 Add content size limit (default 1MB) with truncation warning
- [x] 1.6 Implement `crawl_urls(urls)` method for batch crawling with asyncio.gather()

## 2. Backend: URL Ingestion Endpoint

- [x] 2.1 Add `POST /api/knowledge-bases/{id}/urls` endpoint in knowledge.py
- [x] 2.2 Accept `{"url": "..."}` for single URL or `{"urls": ["...", "..."]}` for batch
- [x] 2.3 Create DocumentModel record with `file_path` as virtual path `web://{domain}/{path}`
- [x] 2.4 Call `knowledge_base_service.ingest_document()` with crawled content
- [x] 2.5 Return summary with document_id, chunk_count, and metadata
- [x] 2.6 Handle errors: invalid URL, crawl failure, timeout

## 3. Backend: Tests

- [x] 3.1 Add unit tests for WebCrawler: successful crawl, timeout, invalid URL
- [x] 3.2 Add tests for metadata extraction: title, description
- [x] 3.3 Add tests for batch crawling with mixed success/failure
- [x] 3.4 Add integration tests for URL ingestion endpoint

## 4. Frontend: URL Input Component

- [x] 4.1 Add URL input field to knowledge base detail page
- [x] 4.2 Support single URL and batch (multi-line) input
- [x] 4.3 Show crawling progress indicator
- [x] 4.4 Display results: success/failure count, chunk count

## 5. Verification

- [x] 5.1 Run `ruff check src/hecate/ tests/` — zero errors
- [x] 5.2 Run `ruff format --check src/ tests/` — zero errors
- [x] 5.3 Run `mypy src/` — zero errors
- [x] 5.4 Run `python -m pytest tests/ -q` — all tests pass
- [x] 5.5 Run `npm run lint` and `npm run build` in `web/` — zero errors
