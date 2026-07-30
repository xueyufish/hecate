## ADDED Requirements — 新增需求

### Requirement: Single URL Crawling — 单个 URL 爬取
The system SHALL provide a `POST /api/knowledge-bases/{id}/urls` endpoint that accepts a URL, fetches the content, extracts text, and ingests it into the knowledge base.

系统应提供 `POST /api/knowledge-bases/{id}/urls` 端点，接受 URL，获取内容，提取文本，并将其摄入到知识库。

#### Scenario: Crawl and ingest a URL — 爬取并摄入 URL
- **WHEN** a user submits `POST /api/knowledge-bases/{id}/urls` with `{"url": "https://example.com/article"}`
- **THEN** the system SHALL fetch the URL, extract text from HTML, create a DocumentModel record, and ingest into the RAG pipeline
- **当**用户提交 `POST /api/knowledge-bases/{id}/urls`，参数为 `{"url": "https://example.com/article"}`
- **则**系统应获取 URL，从 HTML 提取文本，创建 DocumentModel 记录，并摄入到 RAG 流水线

#### Scenario: Crawl failed — 爬取失败
- **WHEN** the URL returns a non-200 status or times out
- **THEN** the system SHALL return HTTP 422 with an error message indicating the crawl failure
- **当**URL 返回非 200 状态或超时
- **则**系统应返回 HTTP 422，并附带指示爬取失败的错误消息

### Requirement: Batch URL Crawling — 批量 URL 爬取
The system SHALL support submitting multiple URLs in a single request for batch ingestion.

系统应支持在单个请求中提交多个 URL 进行批量摄入。

#### Scenario: Batch crawl — 批量爬取
- **WHEN** a user submits `POST /api/knowledge-bases/{id}/urls` with `{"urls": ["url1", "url2", "url3"]}`
- **THEN** the system SHALL crawl all URLs in parallel, ingest successful ones, and return a summary with success/failure counts
- **当**用户提交 `POST /api/knowledge-bases/{id}/urls`，参数为 `{"urls": ["url1", "url2", "url3"]}`
- **则**系统应并行爬取所有 URL，摄入成功的 URL，并返回包含成功/失败计数的摘要

### Requirement: Metadata Extraction — 元数据提取
The system SHALL extract metadata from crawled HTML including: title (from `<title>` tag), description (from `<meta name="description">`), and source URL.

系统应从爬取的 HTML 中提取元数据，包括：标题（来自 `<title>` 标签）、描述（来自 `<meta name="description">`）和来源 URL。

#### Scenario: Extract title and description — 提取标题和描述
- **WHEN** crawling a page with `<title>My Article</title>` and `<meta name="description" content="Article summary">`
- **THEN** the chunks SHALL have metadata: `{"title": "My Article", "description": "Article summary", "source_url": "https://..."}`
- **当**爬取的页面包含 `<title>My Article</title>` 和 `<meta name="description" content="Article summary">`
- **则**文本块应包含元数据：`{"title": "My Article", "description": "Article summary", "source_url": "https://..."}`

### Requirement: Content Size Limit — 内容大小限制
The system SHALL truncate crawled content to a configurable maximum size (default 1MB) to prevent memory issues with very large pages.

系统应将爬取内容截断到可配置的最大大小（默认 1MB），以防止超大页面导致的内存问题。

#### Scenario: Large page truncation — 大页面截断
- **WHEN** crawling a page with 5MB of text content
- **THEN** the system SHALL truncate to 1MB and log a warning about truncation
- **当**爬取的页面包含 5MB 文本内容
- **则**系统应截断到 1MB 并记录关于截断的警告

### Requirement: URL Input in Frontend — 前端的 URL 输入
The knowledge base detail page SHALL provide a URL input field where users can enter one or more URLs to crawl.

知识库详情页面应提供一个 URL 输入字段，用户可以在其中输入一个或多个 URL 进行爬取。

#### Scenario: Add URL — 添加 URL
- **WHEN** the user enters a URL and clicks "Crawl"
- **THEN** the system SHALL call the crawl API and show progress/status
- **当**用户输入 URL 并点击"爬取"
- **则**系统应调用爬取 API 并显示进度/状态

#### Scenario: Batch URLs — 批量 URL
- **WHEN** the user enters multiple URLs (one per line) and clicks "Crawl"
- **THEN** the system SHALL call the batch crawl API and show progress for each URL
- **当**用户输入多个 URL（每行一个）并点击"爬取"
- **则**系统应调用批量爬取 API 并显示每个 URL 的进度
