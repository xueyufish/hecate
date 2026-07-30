## 1. 搜索器分数分解

- [x] 1.1 更新 `searcher.py` 中的 `HybridSearchResult` — 在现有 `sparse_score` 旁添加 `dense_score: float = 0.0` 字段
- [x] 1.2 更新 `HybridSearcher.search()` — 当模式为 `"hybrid"` 时，在融合前分别运行 dense 和 sparse 搜索（或从 Qdrant prefetch 结果中提取），为每个结果填充 `dense_score` 和 `sparse_score`
- [x] 1.3 更新 `test_hybrid_search.py` 中的现有测试，验证 hybrid 模式结果中 `dense_score` 和 `sparse_score` 被正确填充

## 2. 服务层 — KB 搜索和分块

- [x] 2.1 为 `KnowledgeBaseService` 添加 `search_kb()` 方法 — 接受 kb_id（UUID）、query（str）、mode（str）、limit（int）；查找 `KnowledgeBaseModel.qdrant_collection`，使用集合名称调用 `self.search()`，返回带有分数分解的评分结果
- [x] 2.2 为 `KnowledgeBaseService` 添加 `list_chunks()` 方法 — 接受 kb_id、page、page_size；使用 Qdrant `scroll()` API 对集合点进行分页；返回包含 id、内容预览（截断至 200 字符）、metadata 的分块列表
- [x] 2.3 为 `KnowledgeBaseService` 添加 `compare_modes()` 方法 — 接受 kb_id、query、limit；通过 `asyncio.gather()` 在 dense、sparse 和 hybrid 模式下运行搜索；返回每种模式结果的字典
- [x] 2.4 添加 `get_chunk_count()` 辅助方法 — 使用 Qdrant `count()` API 返回集合中的总点数

## 3. API 层 — 命中测试端点

- [x] 3.1 在 `knowledge.py` 中添加 `POST /api/knowledge-bases/{id}/search` 端点 — 接受 `SearchRequest(query, mode, limit)`，调用 `KnowledgeBaseService.search_kb()`，返回带有分数分解的评分结果
- [x] 3.2 添加 `GET /api/knowledge-bases/{id}/chunks` 端点 — 接受 page/page_size 参数，调用 `KnowledgeBaseService.list_chunks()`，返回分页的分块列表
- [x] 3.3 添加 `POST /api/knowledge-bases/{id}/compare` 端点 — 接受 `CompareRequest(query, limit)`，调用 `KnowledgeBaseService.compare_modes()`，返回 dense/sparse/hybrid 的并列结果
- [x] 3.4 添加 Pydantic 请求/响应模式：`KBSearchRequest`、`KBSearchResultSchema`、`KBChunkSchema`、`KBCompareRequest`、`KBCompareResponse`

## 4. 测试

- [x] 4.1 编写 `tests/test_api/test_kb_hit_testing.py` — 测试搜索端点（有效查询、404 不存在的 KB、422 空查询）、分块端点（分页、空 KB）、比较端点（返回全部 3 种模式）
- [x] 4.2 编写 `tests/test_services/test_rag/test_kb_search_service.py` — 使用 mock Qdrant 测试 `search_kb()`、`list_chunks()` 分页、`compare_modes()` 返回所有模式
- [x] 4.3 全面验证：`ruff check src/hecate/ tests/` + `mypy src/` + `pytest tests/ -q`
