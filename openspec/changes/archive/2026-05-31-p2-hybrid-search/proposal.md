## Why — 动机

目前，Hecate 的知识库搜索仅使用向量相似性（Embedding 搜索）。虽然向量搜索对于语义匹配非常有效，但它在精确关键词匹配方面存在困难——例如搜索订单号 "ORD-12345"、产品代码 "PROD-XYZ" 或精确短语 "退款政策"。混合搜索结合了向量和关键词搜索，以提供两方面的优势：语义理解（向量）和精确匹配（关键词）。

在 P1 的 `EnginePort.knowledge_query()` 实现仅暴露了单个检索方法。关键词搜索功能缺失。P2 混合搜索通过以下方式弥补这一差距：
1. 添加关键词搜索（BM25）引擎
2. 创建混合搜索策略（加权向量 + 关键词结果）
3. 将混合搜索集成到知识库查询管道中
4. 为各层（引擎端口、服务 API、Qdrant 配置）暴露配置

## What Changes — 变更内容

- **关键词搜索引擎**: BM25 倒排索引实现，用于文档检索。在 Qdrant（具有全文过滤）和内存中的纯 Python BM25（用于测试/轻量级部署）之间二选一的适配器模式。
- **混合搜索策略**: 加权融合——向量和关键词结果的倒数秩融合（RRF），可调权重，top-K 合并。
- **引擎端口集成**: `EnginePort.knowledge_query()` 接受 `search_mode: str` 参数（"vector"、"keyword"、"hybrid"）以选择搜索策略。
- **知识库 API**: `GET /api/knowledge-bases/{id}/query` 接受 `search_mode` 参数，转发到引擎端口。
- **配置**: 全局搜索模式默认值、混合搜索权重（alpha: 向量 vs 关键词比率）、每个知识库搜索模式覆盖。

## Capabilities — 能力

### New Capabilities — 新增能力
- `keyword-search`: BM25 关键词搜索——文档倒排索引、精确术语匹配、短语搜索、可配置的 BM25 参数（k1、b）
- `hybrid-search`: RRF 融合——结合向量和关键词结果、可调权重（alpha）、top-K 合并、每个知识库搜索模式配置

### Modified Capabilities — 修改的能力
- **知识库查询 API**: `GET /api/knowledge-bases/{id}/query` 添加 `search_mode` 查询参数（vector|keyword|hybrid）
- **EnginePort**: `knowledge_query()` 添加 `search_mode` 和 `search_config` 参数

## Impact — 影响

- **引擎**: `EnginePort.knowledge_query()` 方法签名变更——添加 `search_mode: str = "hybrid"` 和 `search_config: dict | None = None`
- **服务**: 新增 `KeywordSearchService`（BM25 引擎），从 `KnowledgeService` 调用，`HybridSearchStrategy`（RRF 融合）
- **API**: `query` 端点添加 `search_mode` 参数
- **配置**: 核心配置添加 `search` 部分（默认模式、混合权重、BM25 参数）
- **数据库**: 关键词索引须存储或重建——添加 `keyword_index` 表或重建函数
- **依赖**: 关键词搜索引擎——使用 `tantivy`（Rust，通过 Python 绑定）用于生产 BM25，或纯 Python `rank_bm25` 用于开发/测试
