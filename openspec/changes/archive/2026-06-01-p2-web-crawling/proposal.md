## Why

Users currently need to upload files (PDF, DOCX, etc.) to add content to knowledge bases. Many valuable knowledge sources are on the web — documentation sites, blog posts, wikis. The feature catalog describes this as "从 URL 抓取内容作为知识源" with reference to Crawl4AI.

This change adds a web crawling capability that allows users to provide URLs and have the content automatically fetched, parsed, and ingested into the RAG pipeline.

## What Changes

- Add **WebCrawler service** — fetches URL content using `httpx`, parses HTML to text using existing `BeautifulSoup` parser
- Add **URL ingestion endpoint** — `POST /api/knowledge-bases/{id}/urls` to crawl and ingest a URL
- Add **Batch URL ingestion** — support multiple URLs in one request
- Add **Metadata extraction** — extract title, description, and other metadata from HTML
- Add **Frontend URL input** — add URL input field to knowledge base detail page

## Capabilities

### New Capabilities
- `web-crawling`: URL fetching, HTML parsing, and ingestion into RAG pipeline

### Modified Capabilities
- (none — existing parser already supports HTML)

## Impact

- **Backend**: New `src/hecate/services/rag/crawler.py` service
- **Backend**: New endpoint in `src/hecate/api/management/knowledge.py`
- **Frontend**: URL input component in knowledge base detail page
- **Dependencies**: Add `httpx` (already in project), `beautifulsoup4` (already optional)
- **Tests**: Crawler service tests, API endpoint tests
