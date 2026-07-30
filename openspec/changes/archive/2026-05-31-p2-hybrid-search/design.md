## Context — 背景

Hecate 当前的知识库搜索管道：
1. `GET /api/knowledge-bases/{id}/query?q=...` → `KnowledgeService.query()`
2. → `EnginePort.knowledge_query(knowledge_base_id, query, top_k)`
3. → 端口适配器调用向量数据库（Qdrant）执行余弦距离搜索
4. → 返回带有嵌入分值的 `(passage_id, excerpt, score)` 结果

向量搜索的问题：短代码、ID、精确短语名和名词被语义相似性"吞噬"。例如，"ORD-12345" 被嵌入为语义令牌而非精确字符序列。BM25 关键词搜索通过在搜索前对文档和查询进行分词，然后通过倒排索引匹配精确术语来解决这个问题。

RAG 系统中的混合搜索是标准做法（例如 LangChain 的 `EnsembleRetriever`、Haystack 的 `HybridRetrieval`、Qdrant 自身的 `prefetch` + `rrf`）。

## Goals / Non-Goals — 目标 / 非目标

**目标:**
- G1: 关键词搜索 BM25 引擎——可搜索文档的倒排索引，BM25 评分，可配置参数（k1、b）
- G2: 混合搜索策略——向量和关键词结果的倒数秩融合（RRF），每个知识库可配置的权重
- G3: EnginePort 集成——`knowledge_query()` 支持 search_mode（vector|keyword|hybrid），通过配置
- G4: 按需索引——在文档添加到知识库时构建/更新关键词索引
- G5: 知识库 API 扩展——查询端点接受 search_mode 参数

**非目标:**
- 查询补全/建议——P3
- 多字段搜索（标题 + 正文 + 元数据）——P3
- 分面搜索——P4
- 搜索点击率分析和学习排序——P4
- Qdrant 原生混合（全文 + 向量）——P3，因为需要独立的 Qdrant 配置

## Decisions — 决策

### D1: 关键词搜索适配器

**决策**: `KeywordSearchEngine` 抽象基类，两个实现：
- `TantivyKeywordEngine`: 基于 Tantivy（Rust BM25，通过 `tantivy` Python 包）的生产关键词搜索
- `RankBM25Engine`: 开发/测试的纯 Python BM25（`rank_bm25` 包）

**理由**: Tantivy 提供高性能 BM25，适合生产。Rank BM25 用于测试，避免开发环境中的 Rust 编译依赖。

**考虑的替代方案**: Qdrant 全文过滤——被拒绝，因为它需要 P3 的 Qdrant 重新配置。仅使用 `whoosh`——被拒绝，因为 Tantivy 更快且更现代。

### D2: RRF 用于混合融合

**决策**: 使用倒数秩融合（RRF）结合向量和关键词结果。RRF 是确定性且无参数的（除常数 k 外，通常为 60），这使其比加权线性组合更简单，后者需要调优 alpha。

**公式**: `score(d) = 1/(k + rank_vector(d)) + 1/(k + rank_keyword(d))`

**理由**: RRF 被广泛使用（Qdrant、Elasticsearch、Haystack）且效果良好，无需调参。它在考虑排名位置的同时防止单一结果来源主导。

**考虑的替代方案**: 加权线性组合（alpha * vector_score + (1-alpha) * keyword_score）——被拒绝，因为分数尺度不兼容（向量为余弦，BM25 为原始频率）。学习排序（LTR）——P4。

### D3: 通过 EnginePort 传递 search_mode

**决策**: `EnginePort.knowledge_query()` 获取 `search_mode: str = "hybrid"` 和 `search_config: dict | None = None`，向下传递到知识库适配器。

**理由**: search_mode 是 EnginePort 的标准查询参数。它由 API 层确定（来自查询参数或知识库配置），并在调用链中向下传递。

**签名**:
```python
async def knowledge_query(
    self,
    knowledge_base_id: UUID,
    query: str,
    top_k: int = 5,
    search_mode: str = "hybrid",  # "vector" | "keyword" | "hybrid"
    search_config: dict | None = None,  # 每个搜索调用的配置覆盖
) -> list[dict]: ...
```

### D4: 按需索引更新

**决策**: 关键词索引在文档添加到知识库时更新，而非通过批量重建。添加方法 `KnowledgeService.add_document()` → 同时更新向量存储和关键词索引。

**理由**: 实时索引确保新文档立即可搜索。批量重建会增加应用程序启动时的延迟。

### D5: RRF 的配置

**决策**: RRF 常量 k 和 top-K 在核心配置中配置（`settings.search.rrf_k = 60`, `settings.search.rrf_top_k = 10`）。每个知识库可在知识库元数据中覆盖设置。

**理由**: 标准 RRF k=60 适用于大多数情况。每个知识库的覆盖提供了对混合行为的精细控制。

## Risks / Trade-offs — 风险与权衡

- **[风险] Tantivy 编译**: Tantivy 需要 Rust 编译器。→ 缓解措施：使其为可选依赖（`[rag]` extras 组），在缺失时优雅降级至 rank_bm25。文档化安装，可选择 `uv pip install "hecate[rag]"`。
- **[风险] 索引同步**: 关键词索引可能与向量存储不同步。→ 缓解措施：提供 `rebuild_index(knowledge_base_id)` 管理端点，用于手动重新索引。
- **[权衡] RRF 与加权线性组合**: RRF 较简单，但未考虑绝对分数。→ 如果 RRF 在特定知识库上效果不佳，每个知识库可切换到加权搜索模式。加权搜索推迟到 P3。
