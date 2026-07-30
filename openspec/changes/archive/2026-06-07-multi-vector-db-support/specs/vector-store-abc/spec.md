## ADDED Requirements — 新增的需求

### Requirement: VectorStore ABC defines backend-agnostic vector operations — 需求：VectorStore ABC 定义与后端无关的向量操作
`services/rag/vector_store.py` 中的 `VectorStore` 抽象基类应定义所有向量存储后端的接口：`create_collection`、`delete_collection`、`collection_exists`、`upsert`、`delete_by_ids`、`search_dense`、`search_sparse`、`count` 和 `scroll`。所有方法应为异步，并使用 `services/rag/types.py` 中的共享 `SearchResult` 类型。

#### Scenario: ABC is not instantiable — 场景：ABC 不可实例化
- **WHEN — 当** 直接调用 `VectorStore()`
- **THEN — 则** 应引发 `TypeError`，因为抽象方法未实现

#### Scenario: Complete adapter implementation — 场景：完整的适配器实现
- **WHEN — 当** 子类实现所有抽象方法
- **THEN — 则** 应可无错误地实例化

### Requirement: VectorStore provides optional hybrid search with application-layer RRF fallback — 需求：VectorStore 提供带应用层 RRF 回退的可选混合搜索
`VectorStore` ABC 应定义 `search_hybrid()` 作为非抽象方法，其默认实现使用 `search_dense()` 和 `search_sparse()` 执行应用层 RRF 融合，带 4× 预取因子和 k=60。

#### Scenario: Default hybrid search (application-layer RRF) — 场景：默认混合搜索（应用层 RRF）
- **WHEN — 当** 在不覆盖它的后端上调用 `search_hybrid(dense_query, sparse_query, top_k=10)`
- **THEN — 则** 默认实现应调用 `search_dense(dense_query, top_k=40)` 和 `search_sparse(sparse_query, top_k=40)`，通过 k=60 的 RRF 融合，并返回前 10 个结果

#### Scenario: Native hybrid override — 场景：原生混合覆盖
- **WHEN — 当** 后端覆盖 `search_hybrid()`（例如 QdrantVectorStore）
- **THEN — 则** 应使用后端的原生混合查询机制替代默认的 RRF 融合

#### Scenario: supports_hybrid property — 场景：supports_hybrid 属性
- **WHEN — 当** 检查后端的 `supports_hybrid`
- **THEN — 则** 如果后端使用原生实现覆盖了 `search_hybrid()` 应返回 `True`，或如果使用默认的应用层回退则返回 `False`

### Requirement: VectorStore factory instantiates correct backend — 需求：VectorStore 工厂实例化正确的后端
`services/rag/factory.py` 中的 `get_vector_store()` 工厂函数应从设置中读取 `VECTOR_STORE_TYPE` 并返回对应的 `VectorStore` 实例。未知类型应引发 `ValueError`。

#### Scenario: Qdrant backend selection — 场景：Qdrant 后端选择
- **WHEN — 当** `VECTOR_STORE_TYPE=qdrant`
- **THEN — 则** `get_vector_store()` 应返回使用 `QDRANT_URL` 配置的 `QdrantVectorStore` 实例

#### Scenario: Chroma backend selection — 场景：Chroma 后端选择
- **WHEN — 当** `VECTOR_STORE_TYPE=chroma`
- **THEN — 则** `get_vector_store()` 应返回使用 `CHROMA_PERSIST_DIR` 配置的 `ChromaVectorStore` 实例

#### Scenario: Unknown backend type — 场景：未知后端类型
- **WHEN — 当** `VECTOR_STORE_TYPE` 设置为不支持的值（例如 "milvus"）
- **THEN — 则** `get_vector_store()` 应引发 `ValueError` 并附带列出支持类型的消息

### Requirement: QdrantVectorStore implements VectorStore for Qdrant — 需求：QdrantVectorStore 为 Qdrant 实现 VectorStore
`services/rag/qdrant_store.py` 中的 `QdrantVectorStore` 应通过委托给 Qdrant 客户端来实现所有 `VectorStore` 抽象方法。它应覆盖 `search_hybrid()` 以使用 Qdrant 的原生 RRF 融合（通过 `Prefetch + FusionQuery`）。当未安装 `qdrant-client` 时，它应支持模拟回退。

#### Scenario: Lazy client initialization — 场景：惰性客户端初始化
- **WHEN — 当** 实例化 `QdrantVectorStore`
- **THEN — 则** Qdrant 客户端在第一次操作之前不应被创建（惰性加载）

#### Scenario: Mock fallback — 场景：模拟回退
- **WHEN — 当** 未安装 `qdrant-client`
- **THEN — 则** 所有操作应返回确定性的模拟结果（匹配当前 `QdrantIndexer` 行为）

#### Scenario: Native hybrid search — 场景：原生混合搜索
- **WHEN — 当** 在 `QdrantVectorStore` 上调用 `search_hybrid()`
- **THEN — 则** 应在单个查询中使用 Qdrant 的 `Prefetch`（稠密 + 稀疏）和 `Fusion.RRF`

#### Scenario: supports_hybrid returns True — 场景：supports_hybrid 返回 True
- **WHEN — 当** 在 `QdrantVectorStore` 上检查 `supports_hybrid`
- **THEN — 则** 应返回 `True`

### Requirement: ChromaVectorStore implements VectorStore for Chroma — 需求：ChromaVectorStore 为 Chroma 实现 VectorStore
`services/rag/chroma_store.py` 中的 `ChromaVectorStore` 应使用 `chromadb` 库实现所有 `VectorStore` 抽象方法。它不应覆盖 `search_hybrid()`，继承默认的应用层 RRF 回退。当未安装 `chromadb` 时，它应支持模拟回退。

#### Scenario: Local persistence — 场景：本地持久化
- **WHEN — 当** 创建 `ChromaVectorStore(persist_dir="./data/chroma")`
- **THEN — 则** 应使用 `chromadb.PersistentClient` 并传入给定目录

#### Scenario: Dense vector search — 场景：稠密向量搜索
- **WHEN — 当** 调用 `search_dense(collection_name, query_vector, limit)`
- **THEN — 则** 应使用余弦相似度查询 Chroma 的默认集合

#### Scenario: Sparse search returns empty — 场景：稀疏搜索返回空
- **WHEN — 当** 调用 `search_sparse(collection_name, query_sparse, limit)`
- **THEN — 则** 应返回空列表（Chroma 不支持稀疏/BM25 搜索）并记录警告

#### Scenario: Mock fallback — 场景：模拟回退
- **WHEN — 当** 未安装 `chromadb`
- **THEN — 则** 所有操作应返回确定性的模拟结果

#### Scenario: supports_hybrid returns False — 场景：supports_hybrid 返回 False
- **WHEN — 当** 在 `ChromaVectorStore` 上检查 `supports_hybrid`
- **THEN — 则** 应返回 `False`

### Requirement: SearchResult type shared across backends — 需求：跨后端共享的 SearchResult 类型
`SearchResult` dataclass 应在 `services/rag/types.py` 中定义，包含字段 `id: str`、`score: float` 和 `payload: dict[str, Any]`。所有 `VectorStore` 实现应返回此类型。

#### Scenario: SearchResult imported by ABC and adapters — 场景：SearchResult 被 ABC 和适配器导入
- **WHEN — 当** 任何 `VectorStore` 实现返回搜索结果
- **THEN — 则** 每个结果应为 `SearchResult` 实例，包含 `id`、`score` 和 `payload` 字段
