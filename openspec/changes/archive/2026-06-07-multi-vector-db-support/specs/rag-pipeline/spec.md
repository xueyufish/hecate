## MODIFIED Requirements — 修改的需求

### Requirement: QdrantIndexer manages collections with dense+sparse vectors — 需求：QdrantIndexer 使用稠密+稀疏向量管理集合
`QdrantIndexer` 类应被 `VectorStore` ABC 和 `QdrantVectorStore` 适配器替换。所有对 `qdrant_indexer` 单例的直接引用应被对 `get_vector_store()` 工厂的调用替换。适配器应在所有操作上保持与之前 `QdrantIndexer` 相同的行为：使用稠密/稀疏配置创建集合、向量 upsert、稠密/稀疏/混合搜索、scroll、count 和稀疏向量检测。

#### Scenario: Create collection with sparse config — 场景：使用稀疏配置创建集合
- **WHEN — 当** 通过任何 VectorStore 适配器调用 `create_collection(name, with_sparse=True)`
- **THEN — 则** 底层后端应创建具有稠密向量配置和稀疏向量配置（如果支持）的集合

#### Scenario: Upsert with sparse vectors — 场景：使用稀疏向量的 Upsert
- **WHEN — 当** 使用稀疏向量调用 `upsert()`
- **THEN — 则** 每个点应同时具有稠密和稀疏向量表示（如果后端支持稀疏）

#### Scenario: Hybrid search with RRF fusion — 场景：带 RRF 融合的混合搜索
- **WHEN — 当** 调用 `search_hybrid()`
- **THEN — 则** 支持原生混合的后端（Qdrant）应使用服务端 RRF；不支持的后端（Chroma）应使用带 4× 预取和 k=60 的应用层 RRF

#### Scenario: Mock fallback — 场景：模拟回退
- **WHEN — 当** 后端客户端库未安装
- **THEN — 则** 所有操作应使用返回确定性结果的模拟实现

### Requirement: HybridSearcher fuses dense and sparse results — 需求：HybridSearcher 融合稠密和稀疏结果
`HybridSearcher` 应使用 `VectorStore.search_hybrid()` 替代直接调用 `qdrant_indexer`。它应接受 `VectorStore` 实例（通过构造函数注入）并将所有搜索操作委托给它。搜索器不应再导入或引用 `qdrant_indexer`。

#### Scenario: Hybrid search via VectorStore — 场景：通过 VectorStore 的混合搜索
- **WHEN — 当** 调用 `HybridSearcher.search(collection_name, query, limit, mode="hybrid")`
- **THEN — 则** 应调用 `vector_store.search_hybrid()`，透明地使用原生或回退融合

#### Scenario: Dense-only search via VectorStore — 场景：通过 VectorStore 的仅稠密搜索
- **WHEN — 当** 指定 `mode="dense"`
- **THEN — 则** 仅应调用 `vector_store.search_dense()`

#### Scenario: Sparse-only search via VectorStore — 场景：通过 VectorStore 的仅稀疏搜索
- **WHEN — 当** 指定 `mode="sparse"`
- **THEN — 则** 仅应调用 `vector_store.search_sparse()`

#### Scenario: Score breakdown on hybrid results — 场景：混合结果的分数分解
- **WHEN — 当** 执行混合搜索时
- **THEN — 则** 搜索器应并行运行稠密和稀疏搜索以及混合搜索，以在每个结果上填充 `dense_score` 和 `sparse_score`

#### Scenario: Hybrid search with fallback when sparse unavailable — 场景：稀疏不可用时带回退的混合搜索
- **WHEN — 当** 稀疏向量不可用（无稀疏嵌入或后端对稀疏返回空）
- **THEN — 则** 应回退到仅稠密搜索并记录警告

### Requirement: KnowledgeBaseService orchestrates the full RAG pipeline — 需求：KnowledgeBaseService 编排完整的 RAG 管道
`KnowledgeBaseService` 应使用 `get_vector_store()` 工厂替代 `qdrant_indexer` 单例。`reindex_with_sparse()` 方法不应直接访问向量存储的私有客户端——它应使用公共的 `VectorStore` 方法（`scroll`、`upsert`）。

#### Scenario: Document ingestion pipeline — 场景：文档导入管道
- **WHEN — 当** 调用 `ingest_document(file_path, collection_name)`
- **THEN — 则** 应解析 → 分块 → 编码（稠密+稀疏）→ `vector_store.upsert()`，返回 `{"chunk_count": N, "collection": name}`

#### Scenario: Text ingestion (pre-extracted) — 场景：文本导入（预提取）
- **WHEN — 当** 调用 `ingest_document_text(text, collection_name)`
- **THEN — 则** 应分块 → 编码 → `vector_store.upsert()`，跳过解析步骤

#### Scenario: Search with mode selection — 场景：带模式选择的搜索
- **WHEN — 当** 调用 `search(collection_name, query, mode="hybrid")`
- **THEN — 则** 应委托给 `HybridSearcher.search()`，它使用 `VectorStore.search_hybrid()`

#### Scenario: Re-index with sparse vectors encapsulation — 场景：使用稀疏向量重新索引的封装
- **WHEN — 当** 调用 `reindex_with_sparse(collection_name)`
- **THEN — 则** 应使用 `vector_store.scroll()` 迭代点并使用 `vector_store.upsert()` 更新，不访问任何私有客户端属性

#### Scenario: List chunks with pagination — 场景：带分页的列出块
- **WHEN — 当** 调用 `list_chunks(collection_name, page, page_size)`
- **THEN — 则** 应使用 `vector_store.scroll()` 进行基于游标的分页
