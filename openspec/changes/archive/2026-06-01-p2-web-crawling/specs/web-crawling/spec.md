## ADDED Requirements

### Requirement: Single URL Crawling
The system SHALL provide a `POST /api/knowledge-bases/{id}/urls` endpoint that accepts a URL, fetches the content, extracts text, and ingests it into the knowledge base.

#### Scenario: Crawl and ingest a URL
- **WHEN** a user submits `POST /api/knowledge-bases/{id}/urls` with `{"url": "https://example.com/article"}`
- **THEN** the system SHALL fetch the URL, extract text from HTML, create a DocumentModel record, and ingest into the RAG pipeline

#### Scenario: Crawl failed
- **WHEN** the URL returns a non-200 status or times out
- **THEN** the system SHALL return HTTP 422 with an error message indicating the crawl failure

### Requirement: Batch URL Crawling
The system SHALL support submitting multiple URLs in a single request for batch ingestion.

#### Scenario: Batch crawl
- **WHEN** a user submits `POST /api/knowledge-bases/{id}/urls` with `{"urls": ["url1", "url2", "url3"]}`
- **THEN** the system SHALL crawl all URLs in parallel, ingest successful ones, and return a summary with success/failure counts

### Requirement: Metadata Extraction
The system SHALL extract metadata from crawled HTML including: title (from `<title>` tag), description (from `<meta name="description">`), and source URL.

#### Scenario: Extract title and description
- **WHEN** crawling a page with `<title>My Article</title>` and `<meta name="description" content="Article summary">`
- **THEN** the chunks SHALL have metadata: `{"title": "My Article", "description": "Article summary", "source_url": "https://..."}`

### Requirement: Content Size Limit
The system SHALL truncate crawled content to a configurable maximum size (default 1MB) to prevent memory issues with very large pages.

#### Scenario: Large page truncation
- **WHEN** crawling a page with 5MB of text content
- **THEN** the system SHALL truncate to 1MB and log a warning about truncation

### Requirement: URL Input in Frontend
The knowledge base detail page SHALL provide a URL input field where users can enter one or more URLs to crawl.

#### Scenario: Add URL
- **WHEN** the user enters a URL and clicks "Crawl"
- **THEN** the system SHALL call the crawl API and show progress/status

#### Scenario: Batch URLs
- **WHEN** the user enters multiple URLs (one per line) and clicks "Crawl"
- **THEN** the system SHALL call the batch crawl API and show progress for each URL
