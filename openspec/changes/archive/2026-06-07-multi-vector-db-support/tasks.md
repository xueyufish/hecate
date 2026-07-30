## 1. 共享类型 & ABC 基础

- [x] 1.1 将 `SearchResult` dataclass 从 `services/rag/indexer.py` 移动到 `services/rag/types.py`（更新所有导入）
- [x] 1.2 创建 `services/rag/vector_store.py` 及 `VectorStore` ABC：抽象方法 `create_collection`、`delete_collection`、`collection_exists`、`upsert`、`delete_by_ids`、`search_dense`、`search_sparse`、`count`、`scroll`；非抽象 `search_hybrid` 带默认 RRF 融合（4× 预取，k=60）；属性 `supports_hybrid` 默认为 `False`
- [x] 1.3 在 `vector_store.py` 中添加私有 `_rrf_fuse()` 辅助函数，实现标准 RRF：`score(d) = Σ 1/(k + rank_i(d))`，k=60，基于 1 的排名

## 2. Qdrant 适配器

- [x] 2.1 创建 `services/rag/qdrant_store.py` 及 `QdrantVectorStore(VectorStore)`——将所有 `QdrantIndexer` 方法的逻辑迁移到对应的 ABC 方法，保留模拟回退和惰性客户端初始化
- [x] 2.2 在 `QdrantVectorStore` 中覆盖 `search_hybrid()` 以使用 Qdrant 原生 `Prefetch + Fusion.RRF`；设置 `supports_hybrid = True`
- [ ] 2.3 验证 `QdrantVectorStore` 通过所有先前测试 `QdrantIndexer` 行为的现有测试

## 3. 工厂 & 配置

- [x] 3.1 在 `core/config.py` Settings 中添加：`VECTOR_STORE_TYPE: str = "qdrant"`、`CHROMA_PERSIST_DIR: str = "./data/chroma"`；保留现有 `QDRANT_URL`
- [x] 3.2 创建 `services/rag/factory.py` 及 `get_vector_store() -> VectorStore`，在 `settings.vector_store_type` 上使用 match/case
- [x] 3.3 使用 `VECTOR_STORE_TYPE`、`CHROMA_PERSIST_DIR` 条目更新 `.env.example`

## 4. 列重命名（破坏性变更）

- [x] 4.1 生成 Alembic 迁移：将 `knowledge_bases` 表上的 `qdrant_collection` 重命名为 `collection_name`（包括升级和降级）
- [x] 4.2 更新 `models/knowledge.py`：将 `qdrant_collection` 属性重命名为 `collection_name`，更新 `mapped_column("collection_name", String(255))`
- [x] 4.3 更新 Pydantic schemas：在 CreateSchema 和 ReadSchema 中将 `qdrant_collection` 重命名为 `collection_name`
- [x] 4.4 更新 `services/orchestration/agent_execution_port.py`：将 `kb.qdrant_collection` → `kb.collection_name` 引用

## 5. 消费者重构

- [x] 5.1 重构 `services/rag/searcher.py`：`HybridSearcher` 通过构造函数注入接受 `VectorStore`；将所有 `qdrant_indexer` 调用替换为 `self._store` 调用；移除 `from indexer import qdrant_indexer`
- [x] 5.2 重构 `services/rag/service.py`：`KnowledgeBaseService` 使用 `get_vector_store()` 工厂；将 `qdrant_indexer` 替换为工厂调用；修复 `reindex_with_sparse()` 以使用 `scroll()` + `upsert()` 替代私有客户端访问
- [x] 5.3 移除模块级单例：从 `indexer.py` 删除 `qdrant_indexer = QdrantIndexer()` 和从 `searcher.py` 删除 `hybrid_searcher = HybridSearcher()`；更新所有导入点
- [x] 5.4 在所有消费者迁移后删除或弃用 `services/rag/indexer.py`（旧的 `QdrantIndexer` 类）

## 6. Chroma 适配器

- [x] 6.1 在 `pyproject.toml` 中将 `chromadb` 添加到 `[rag]` 可选依赖组
- [x] 6.2 创建 `services/rag/chroma_store.py` 及 `ChromaVectorStore(VectorStore)`——使用 `chromadb.PersistentClient` 实现所有抽象方法；`search_sparse()` 返回空列表并带警告（Chroma 无 BM25）；未安装 `chromadb` 时模拟回退
- [x] 6.3 不覆盖 `search_hybrid()`——继承默认应用层 RRF；`supports_hybrid` 返回 `False`

## 7. 测试

- [x] 7.1 测试 `VectorStore` ABC：验证不能直接实例化；验证完整的子类正常工作
- [x] 7.2 测试 `QdrantVectorStore`：集合 CRUD、upsert、search_dense、search_sparse、search_hybrid（原生）、scroll、count、模拟回退
- [x] 7.3 测试 `ChromaVectorStore`：集合 CRUD、upsert、search_dense、search_sparse（返回空）、scroll、count、模拟回退
- [x] 7.4 测试 `_rrf_fuse()`：验证 k=60、基于 1 的排名、正确排序、跨通道去重的 RRF 公式
- [x] 7.5 测试 `get_vector_store()` 工厂：对 "qdrant"/"chroma" 返回正确类型，对未知类型引发 ValueError
- [x] 7.6 测试带模拟 VectorStore 的 `HybridSearcher`：混合模式调用 `search_hybrid`，稠密调用 `search_dense`，稀疏调用 `search_sparse`，稀疏不可用时回退
- [x] 7.7 测试带模拟 VectorStore 的 `KnowledgeBaseService`：导入正确委托，搜索委托给 HybridSearcher，重新索引使用 scroll+upsert 无私有访问
- [x] 7.8 更新引用 `qdrant_indexer` 的现有 RAG 测试以使用 `get_vector_store()` 或模拟 VectorStore

## 8. 验证

- [x] 8.1 运行 `ruff check src/hecate/ tests/`——零错误
- [x] 8.2 运行 `ruff format --check src/ tests/`——零错误
- [x] 8.3 运行 `mypy src/`——零错误
- [x] 8.4 运行 `python -m pytest tests/ -q`——所有测试通过
