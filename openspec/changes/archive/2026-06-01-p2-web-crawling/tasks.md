## 1. Backend: WebCrawler Service — 后端：WebCrawler 服务

- [x] 1.1 Create `src/hecate/services/rag/crawler.py` with `WebCrawler` class — 创建包含 `WebCrawler` 类的 `src/hecate/services/rag/crawler.py`
- [x] 1.2 Implement `crawl_url(url)` method that fetches URL content using httpx with timeout — 实现 `crawl_url(url)` 方法，使用 httpx 获取 URL 内容并设置超时
- [x] 1.3 Implement HTML text extraction using BeautifulSoup (reuse parser logic) — 使用 BeautifulSoup 实现 HTML 文本提取（复用解析器逻辑）
- [x] 1.4 Extract metadata: title from `<title>`, description from `<meta name="description">` — 提取元数据：从 `<title>` 提取标题，从 `<meta name="description">` 提取描述
- [x] 1.5 Add content size limit (default 1MB) with truncation warning — 添加内容大小限制（默认 1MB）并附带截断警告
- [x] 1.6 Implement `crawl_urls(urls)` method for batch crawling with asyncio.gather() — 实现 `crawl_urls(urls)` 方法，使用 asyncio.gather() 进行批量爬取

## 2. Backend: URL Ingestion Endpoint — 后端：URL 摄入端点

- [x] 2.1 Add `POST /api/knowledge-bases/{id}/urls` endpoint in knowledge.py — 在 knowledge.py 中添加 `POST /api/knowledge-bases/{id}/urls` 端点
- [x] 2.2 Accept `{"url": "..."}` for single URL or `{"urls": ["...", "..."]}` for batch — 接受 `{"url": "..."}`（单个 URL）或 `{"urls": ["...", "..."]}`（批量）
- [x] 2.3 Create DocumentModel record with `file_path` as virtual path `web://{domain}/{path}` — 创建 DocumentModel 记录，`file_path` 设置为虚拟路径 `web://{domain}/{path}`
- [x] 2.4 Call `knowledge_base_service.ingest_document()` with crawled content — 使用爬取的内容调用 `knowledge_base_service.ingest_document()`
- [x] 2.5 Return summary with document_id, chunk_count, and metadata — 返回包含 document_id、chunk_count 和元数据的摘要
- [x] 2.6 Handle errors: invalid URL, crawl failure, timeout — 处理错误：无效 URL、爬取失败、超时

## 3. Backend: Tests — 后端：测试

- [x] 3.1 Add unit tests for WebCrawler: successful crawl, timeout, invalid URL — 为 WebCrawler 添加单元测试：成功爬取、超时、无效 URL
- [x] 3.2 Add tests for metadata extraction: title, description — 添加元数据提取测试：标题、描述
- [x] 3.3 Add tests for batch crawling with mixed success/failure — 添加混合成功/失败的批量爬取测试
- [x] 3.4 Add integration tests for URL ingestion endpoint — 为 URL 摄入端点添加集成测试

## 4. Frontend: URL Input Component — 前端：URL 输入组件

- [x] 4.1 Add URL input field to knowledge base detail page — 在知识库详情页面添加 URL 输入字段
- [x] 4.2 Support single URL and batch (multi-line) input — 支持单个 URL 和批量（多行）输入
- [x] 4.3 Show crawling progress indicator — 显示爬取进度指示器
- [x] 4.4 Display results: success/failure count, chunk count — 显示结果：成功/失败计数、文本块计数

## 5. Verification — 验证

- [x] 5.1 Run `ruff check src/hecate/ tests/` — zero errors — 运行 `ruff check src/hecate/ tests/` — 零错误
- [x] 5.2 Run `ruff format --check src/ tests/` — zero errors — 运行 `ruff format --check src/ tests/` — 零错误
- [x] 5.3 Run `mypy src/` — zero errors — 运行 `mypy src/` — 零错误
- [x] 5.4 Run `python -m pytest tests/ -q` — all tests pass — 运行 `python -m pytest tests/ -q` — 所有测试通过
- [x] 5.5 Run `npm run lint` and `npm run build` in `web/` — zero errors — 在 `web/` 中运行 `npm run lint` 和 `npm run build` — 零错误
