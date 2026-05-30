## Context

Hecate 的 RAG 检索层目前只实现了 3.2.1（稠密向量检索）。核心代码在 `src/hecate/services/rag/` 下：

- **`EmbeddingService`** — 使用 BAAI/bge-m3 模型，`EmbeddingResult` 已预留 `sparse` 字段但永远返回空 `{}`
- **`QdrantIndexer`** — `create_collection()` 只配置稠密向量，`search()` 只做 ANN 搜索
- **`HybridSearcher`** — 类名暗示混合检索，但 `search()` 只调稠密检索，`sparse_weight` 从未使用
- **`KnowledgeBaseService`** — `search()` 委托给 `HybridSearcher`，`ingest_document()` 只存稠密向量
- **`EnginePort.knowledge_query`** — 抽象接口已定义，但 `AgentExecutionPort` 实现直接 `raise NotImplementedError`

技术栈约束：
- Qdrant 1.12+（已安装），原生支持稀疏向量 + `QueryRequest` fusion API
- BGE-M3 模型（FlagEmbedding）可同时生成稠密和稀疏向量
- Python 3.12+, SQLAlchemy 2.0 async, FastAPI

## Goals / Non-Goals

**Goals:**
- 实现 3.2.2 关键词检索：基于 Qdrant 稀疏向量的 BM25 风格检索
- 实现 3.2.3 混合检索：稠密 + 稀疏分数融合，支持权重配置
- 让 `EnginePort.knowledge_query` 真正可用
- 保持向后兼容：已有知识库无需迁移即可使用新检索能力

**Non-Goals:**
- 不实现独立的 BM25 引擎（如 Elasticsearch），用 Qdrant 原生方案
- 不实现重排序（Reranking）— 这是 P4 的 3.2.5
- 不修改前端 UI — 检索能力变化对前端透明
- 不实现增量稀疏向量更新 — 首次索引时生成全部向量

## Decisions

### Decision 1: Qdrant 原生稀疏向量 vs 独立 BM25 库

**选择：Qdrant 原生稀疏向量**

理由：
- 已有 `qdrant-client>=1.12.0`，零新增依赖
- Qdrant 原生支持 `SparseVectorParams` + `QueryRequest` fusion，无需手动实现分数融合
- 单一存储引擎，运维简单
- 性能：Qdrant 内部用倒排索引处理稀疏向量，接近原生 BM25 性能

**备选方案（放弃）：**
- `rank-bm25` 纯 Python 实现 — 需要自行维护内存倒排索引，无法持久化，大规模不可行
- Elasticsearch — 重量级依赖，运维成本高，与 Qdrant 功能重叠

### Decision 2: 稀疏向量生成方式

**选择：BGE-M3 原生稀疏输出**

理由：
- BGE-M3 本身就是多语言模型，同时支持稠密 + 稀疏 + ColBERT 三种向量
- FlagEmbedding 库（已安装）的 `BGEM3FlagModel` 可以直接输出 sparse embedding
- 稀疏向量格式：`dict[int, float]`（token_id → weight），可直接存入 Qdrant SparseVector

**备选方案（放弃）：**
- `fastembed` 的稀疏模型 — 需要额外依赖，且 BGE-M3 已够用
- 手动 BM25 分词 — 复杂度高，效果不如 BGE-M3 稀疏输出

### Decision 3: 分数融合策略

**选择：Qdrant 内置 Fusion（RRF）+ 可选加权**

理由：
- Qdrant 的 `QueryRequest` 支持 `prefetch` + `fusion`，直接在数据库层完成融合
- RRF（Reciprocal Rank Fusion）是业界标准，不需要归一化分数
- 未来可通过 `QueryRequest` 的权重参数扩展为加权融合

**备选方案（放弃）：**
- 应用层手动 RRF — 增加代码复杂度，且无法利用 Qdrant 的优化
- 简单加权求和 — 需要分数归一化，不同检索方式的分数分布不同

### Decision 4: EnginePort 对接方式

**选择：在 AgentExecutionPort 中注入 KnowledgeBaseService**

理由：
- `AgentExecutionPort` 已经是 `EnginePort` 的具体实现
- `KnowledgeBaseService` 是 RAG 的入口服务，包含 search 方法
- 注入后，`knowledge_query` 直接委托给 `KnowledgeBaseService.search`

## Risks / Trade-offs

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| BGE-M3 稀疏向量内存占用 | 每个文档同时存稠密(4KB) + 稀疏(1KB) 向量 | 监控内存使用，必要时分批索引 |
| Qdrant fusion API 稳定性 | 较新特性，可能有 bug | 做好 fallback：应用层 RRF |
| 已有知识库迁移 | 旧集合没有稀疏向量 | 新建集合并重新索引，或惰性迁移 |
| BGE-M3 加载时间 | 首次加载模型较慢 | 保持现有的懒加载 + 模型缓存 |
