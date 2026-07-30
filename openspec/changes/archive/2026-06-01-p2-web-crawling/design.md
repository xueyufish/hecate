## Context — 背景

The RAG pipeline is modular: `parser → chunker → embedder → indexer`. The `DocumentParser` already supports HTML files via BeautifulSoup. The `KnowledgeBaseService.ingest_document()` orchestrates the full pipeline.

RAG 流水线是模块化的：`parser → chunker → embedder → indexer`。`DocumentParser` 已通过 BeautifulSoup 支持 HTML 文件。`KnowledgeBaseService.ingest_document()` 协调整个流水线。

For web crawling, we need to:
1. Fetch URL content (HTML)
2. Extract clean text (reuse existing HTML parser)
3. Feed into existing pipeline

对于网络爬取，我们需要：
1. 获取 URL 内容（HTML）
2. 提取干净的文本（复用现有的 HTML 解析器）
3. 输入到现有流水线

## Goals / Non-Goals — 目标/非目标

**Goals — 目标：**
- Crawl single URL and ingest into knowledge base
- Batch crawl multiple URLs
- Extract metadata (title, description) from HTML
- Show crawl status in frontend
- Respect robots.txt (basic)

**目标：**
- 爬取单个 URL 并摄入到知识库
- 批量爬取多个 URL
- 从 HTML 提取元数据（标题、描述）
- 在前端显示爬取状态
- 遵守 robots.txt（基本）

**Non-Goals — 非目标：**
- Deep crawling (following links recursively) — deferred
- JavaScript rendering (SPA sites) — deferred
- Rate limiting per domain — deferred
- Persistent crawl queue — deferred

**非目标：**
- 深度爬取（递归跟踪链接）— 推迟
- JavaScript 渲染（SPA 站点）— 推迟
- 每域名速率限制 — 推迟
- 持久化爬取队列 — 推迟

## Decisions — 决策

### D1: New WebCrawler service (not extending parser) — 新建 WebCrawler 服务（不扩展解析器）

**Decision — 决策：** Create `src/hecate/services/rag/crawler.py` as a new service that fetches URLs and returns extracted text + metadata.

创建 `src/hecate/services/rag/crawler.py` 作为新服务，用于获取 URL 并返回提取的文本和元数据。

**Rationale — 理由：** Separation of concerns. Parser handles local files, crawler handles URLs. Both feed into the same chunker/embedder/indexer pipeline.

关注点分离。解析器处理本地文件，爬取器处理 URL。两者都输入到相同的 chunker/embedder/indexer 流水线。

### D2: Use httpx for HTTP requests — 使用 httpx 进行 HTTP 请求

**Decision — 决策：** Use `httpx` (already in dependencies) for async HTTP requests.

使用 `httpx`（已在依赖中）进行异步 HTTP 请求。

**Rationale — 理由：** Already available, async-native, good timeout handling.

已可用、原生异步、良好的超时处理。

### D3: BeautifulSoup for HTML parsing — 使用 BeautifulSoup 解析 HTML

**Decision — 决策：** Reuse existing `DocumentParser._parse_html()` logic for text extraction.

复用现有的 `DocumentParser._parse_html()` 逻辑进行文本提取。

**Rationale — 理由：** Already tested, handles common HTML patterns.

已经过测试，可处理常见 HTML 模式。

### D4: Store crawled content as documents — 将爬取内容存储为文档

**Decision — 决策：** Create `DocumentModel` records for crawled URLs with `file_path` set to a virtual path like `web://{domain}/{path}`.

为爬取的 URL 创建 `DocumentModel` 记录，`file_path` 设置为虚拟路径，如 `web://{domain}/{path}`。

**Rationale — 理由：** Consistent with existing document management. Users can see crawled content alongside uploaded files.

与现有文档管理保持一致。用户可以在上传文件旁边看到爬取的内容。

## Risks / Trade-offs — 风险/权衡

- **[No JS rendering — 无 JS 渲染]** → SPA sites (React, Vue) won't render content. Mitigation: document this limitation.
  SPA 站点（React、Vue）不会渲染内容。缓解措施：记录此限制。
- **[No robots.txt — 无 robots.txt]** → May crawl restricted pages. Mitigation: add basic robots.txt check.
  可能会爬取受限页面。缓解措施：添加基本的 robots.txt 检查。
- **[Large pages — 大页面]** → Some pages are very large. Mitigation: truncate to configurable max size.
  某些页面非常大。缓解措施：截断到可配置的最大大小。
