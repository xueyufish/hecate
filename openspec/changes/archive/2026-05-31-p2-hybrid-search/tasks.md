## 1. 引擎层 — 端口变更

- [x] 1.1 更新 `EnginePort.knowledge_query()` 签名——添加 `search_mode: str = "hybrid"` 和 `search_config: dict | None = None` 参数
- [x] 1.2 更新 `InMemoryKnowledgeBaseAdapter`（端口适配器/测试存根）以处理 search_mode 参数

## 2. 服务层 — 关键词搜索引擎

- [x] 2.1 创建 `src/hecate/services/rag/keyword_engine.py`——`KeywordSearchEngine` 抽象基类，方法：`index_documents(knowledge_base_id, documents)`, `search(knowledge_base_id, query, top_k) -> list[dict]`, `remove_document(knowledge_base_id, doc_id)`
- [x] 2.2 实现 `RankBM25Engine`——使用 `rank_bm25` 包的纯 Python BM25，在 `index_documents()` 时构建内存索引，`search()` 时使用 `BM25Okapi`
- [x] 2.3 在 `src/hecate/services/rag/keyword_engine.py` 中实现 `TantivyKeywordEngine`——使用 `tantivy` 包的生产 BM25（`try/except ImportError` 降级），为每个知识库创建独立索引
- [x] 2.4 创建 `src/hecate/services/rag/hybrid_search.py`——`RRFFusionStrategy` 类，方法 `fuse(vector_results, keyword_results, top_k, rrf_k=60) -> list[dict]`，使用倒数秩融合合并及重新排序结果
- [x] 2.5 创建 `src/hecate/services/rag/hybrid_search.py` 中的 `HybridSearchService`——协调对 `KnowledgeBaseService`（向量）和 `KeywordSearchEngine`（关键词）的并行调用，应用 RRF 融合

## 3. API 层 — 查询集成

- [x] 3.1 更新 `GET /api/knowledge-bases/{id}/query`——添加可选的 `search_mode` 查询参数（vector|keyword|hybrid），默认从配置读取
- [x] 3.2 更新 `src/hecate/api/knowledge_bases.py` 中的 `KnowledgeController`——将 search_mode 转发到 `KnowledgeService`，后者随后调用 `HybridSearchService`

## 4. 配置

- [x] 4.1 向核心配置添加 `SearchSettings` 类（在 `src/hecate/core/config.py`）——字段：`default_search_mode: str = "hybrid"`, `rrf_k: int = 60`, `rrf_top_k: int = 10`, `keyword_engine: str = "rank_bm25"`
- [x] 4.2 在 `pyproject.toml` 的 `[project.optional-dependencies]` 下的 `[rag]` 组中添加 `tantivy` 可选依赖

## 5. 测试

- [x] 5.1 创建 `tests/test_services/test_rag/test_keyword_engine.py`——测试 RankBM25Engine 的索引和搜索（有效查询、空结果、多知识库隔离）
- [x] 5.2 创建 `tests/test_services/test_rag/test_hybrid_search.py`——测试 RRF 融合（均等排名、向量主导、关键词主导、top-K 限制、边界情况）
- [x] 5.3 更新 `tests/test_api/test_knowledge_bases.py`——测试 `search_mode` 查询参数（向量、关键词、混合、无效模式）
- [x] 5.4 更新 `tests/test_services/test_rag/test_knowledge_service.py`——测试 search_mode 传播到下层引擎

## 6. 文档

- [x] 6.1 更新 `docs/features/feature-catalog.md`——将 2.3 和 7.2 标记为已实现
