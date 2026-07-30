## MODIFIED Requirements — 修改的需求

### Requirement: Hybrid search combines dense and sparse retrieval — 需求：混合搜索结合稠密和稀疏检索
系统应通过调用 `VectorStore.search_hybrid()` 执行混合搜索，该方法透明地委托给后端的原生混合（对于支持的后端，如 Qdrant）或回退到应用层 RRF 融合（对于不支持的后端，如 Chroma）。`HybridSearcher` 不应再直接引用 `qdrant_indexer`。

#### Scenario: Hybrid search with native backend (Qdrant) — 场景：使用原生后端（Qdrant）的混合搜索
- **WHEN — 当** 使用 QdrantVectorStore 调用 `HybridSearcher.search(collection_name, query, limit, mode="hybrid")`
- **THEN — 则** 系统应通过覆盖的 `search_hybrid()` 方法使用 Qdrant 的原生 `Prefetch + Fusion.RRF`

#### Scenario: Hybrid search with fallback backend (Chroma) — 场景：使用回退后端（Chroma）的混合搜索
- **WHEN — 当** 使用 ChromaVectorStore 调用 `HybridSearcher.search(collection_name, query, limit, mode="hybrid")`
- **THEN — 则** 系统应通过默认的 `search_hybrid()` 方法使用应用层 RRF 融合（4× 预取，k=60）

#### Scenario: Hybrid search with custom weights — 场景：带自定义权重的混合搜索
- **WHEN — 当** 使用自定义 `dense_weight` 和 `sparse_weight` 调用 `HybridSearcher.search()`
- **THEN — 则** 权重应在分数组合期间应用，影响最终排名

#### Scenario: Fallback when sparse vectors unavailable — 场景：稀疏向量不可用时的回退
- **WHEN — 当** 目标集合没有稀疏向量配置或后端不支持稀疏搜索
- **THEN — 则** 系统应回退到仅稠密搜索并记录警告

### Requirement: Knowledge base service exposes hybrid search — 需求：知识库服务暴露混合搜索
`KnowledgeBaseService.search()` 方法应接受 `mode` 参数以控制检索策略："hybrid"（默认）、"dense"（仅向量）、"sparse"（仅关键词）。所有模式应通过 `VectorStore` 方法委托，而非直接委托给任何特定后端。

#### Scenario: Search with hybrid mode — 场景：使用混合模式搜索
- **WHEN — 当** 调用 `KnowledgeBaseService.search(collection_name, query, limit, mode="hybrid")`
- **THEN — 则** 应委托给 `HybridSearcher.search()`，后者调用 `VectorStore.search_hybrid()`

#### Scenario: Search with dense-only mode — 场景：使用仅稠密模式搜索
- **WHEN — 当** 调用 `KnowledgeBaseService.search(collection_name, query, limit, mode="dense")`
- **THEN — 则** 应委托给 `HybridSearcher.search()`，后者调用 `VectorStore.search_dense()`

#### Scenario: Search with sparse-only mode — 场景：使用仅稀疏模式搜索
- **WHEN — 当** 调用 `KnowledgeBaseService.search(collection_name, query, limit, mode="sparse")`
- **THEN — 则** 应委托给 `HybridSearcher.search()`，后者调用 `VectorStore.search_sparse()`

### Requirement: EnginePort knowledge_query wired to RAG services — 需求：EnginePort knowledge_query 连接到 RAG 服务
`AgentExecutionPort.knowledge_query()` 应委托给 `KnowledgeBaseService.search()`，使用 `collection_name`（而非 `qdrant_collection`）查找每个知识库的向量存储集合。

#### Scenario: Engine queries knowledge base — 场景：引擎查询知识库
- **WHEN — 当** 执行引擎在端口上调用 `knowledge_query(query, kb_ids)`
- **THEN — 则** 应查找给定 `kb_ids` 的 `collection_name` 并为每个调用 `KnowledgeBaseService.search()`，聚合结果

#### Scenario: Knowledge base not found — 场景：未找到知识库
- **WHEN — 当** 使用不存在的 `kb_id` 调用 `knowledge_query`
- **THEN — 则** 应返回空列表并记录警告（不引发异常）
