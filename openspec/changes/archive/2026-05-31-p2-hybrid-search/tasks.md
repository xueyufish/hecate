## 1. 稀疏向量生成（EmbeddingService）

- [x] 1.1 更新 `embedding.py` — `encode()` 使用 BGE-M3 的 `encode()` 方法带 `return_dense=True, return_sparse=True`，将 sparse 输出转为 `dict[int, float]`
- [x] 1.2 更新 `encode_query()` — 调用 `encode([query])` 并返回同时包含 dense 和 sparse 的 `EmbeddingResult`
- [x] 1.3 更新 `_mock_embedding()` — 生成确定性的 mock sparse 向量（基于 text hash 的 token_id → weight 映射）

## 2. Qdrant 稀疏向量支持（QdrantIndexer）

- [x] 2.1 更新 `create_collection()` — 添加 `sparse_vectors_config={"sparse": SparseVectorParams(index=models.SparseIndexParams())}` 参数
- [x] 2.2 更新 `upsert_vectors()` — 新增可选参数 `sparse_vectors: list[dict[int, float]] | None = None`，构建 PointStruct 时同时传入 dense 和 sparse 向量
- [x] 2.3 添加 `search_sparse()` 方法 — 使用 Qdrant 的 `query_points()` 做稀疏向量搜索
- [x] 2.4 添加集合配置检测方法 `has_sparse_vectors(collection_name)` — 检查集合是否已配置稀疏向量

## 3. 混合检索实现（HybridSearcher）

- [x] 3.1 重写 `search()` 方法 — 使用 Qdrant `QueryRequest` 的 `prefetch` + `fusion=Models.Fusion.RRF` 实现真正的混合检索
- [x] 3.2 添加 `mode` 参数支持 — `"hybrid"` (默认) / `"dense"` / `"sparse"` 三种模式
- [x] 3.3 实现 fallback 逻辑 — 当集合无稀疏向量配置时，自动降级为 dense-only 并 log warning
- [x] 3.4 更新 `HybridSearchResult` — 添加 `sparse_score` 字段记录稀疏检索分数

## 4. 知识库服务更新（KnowledgeBaseService）

- [x] 4.1 更新 `ingest_document()` — pipeline 中调用 `embedding_service.encode()` 获取 sparse 向量，传入 `qdrant_indexer.upsert_vectors()`
- [x] 4.2 更新 `search()` — 添加 `mode: str = "hybrid"` 参数，委托给 `hybrid_searcher.search()` 时传入 mode
- [x] 4.3 添加 `reindex_with_sparse(collection_name)` 方法 — 重新索引已有集合，为存量文档生成并存储稀疏向量

## 5. EnginePort 对接

- [x] 5.1 更新 `AgentExecutionPort.knowledge_query()` — 注入 `KnowledgeBaseService`，查找 kb_ids 对应的 Qdrant collection names，调用 `search()` 返回结果
- [x] 5.2 添加 `kb_id → collection_name` 映射逻辑 — 查询 `KnowledgeBaseModel` 获取 `qdrant_collection` 字段

## 6. 模型与配置

- [x] 6.1 更新 `KnowledgeBaseModel` — 添加 `search_mode` 字段（默认 `"hybrid"`）和 `sparse_weight` 字段（默认 `0.3`）
- [x] 6.2 生成并执行 Alembic 迁移脚本

## 7. 测试

- [x] 7.1 编写 `test_embedding_sparse.py` — 测试稀疏向量生成（encode/encode_query/mock）
- [x] 7.2 编写 `test_hybrid_search.py` — 测试混合检索（hybrid/dense/sparse 模式、fallback）
- [x] 7.3 编写 `test_knowledge_service.py` — 测试 ingest 带稀疏向量、search 多模式
- [x] 7.4 编写 `test_engine_port_knowledge.py` — 测试 EnginePort.knowledge_query 真实调用
- [x] 7.5 全量验证：`ruff check src/` + `mypy src/` + `pytest tests/ -q`
