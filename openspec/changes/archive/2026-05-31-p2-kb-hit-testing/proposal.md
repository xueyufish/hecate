## Why — 动机

用户创建知识库、上传文档，并配置带有 RAG 的 Agent，但在部署前无法验证检索质量。他们无法查看存储了哪些分块、查询是否返回相关结果，以及不同搜索模式（dense/sparse/hybrid）如何影响排序。这造成了信任缺口：RAG 要么工作要么不工作，用户无法调试。

混合搜索基础设施（3.2.2 + 3.2.3）已经完成 — `KnowledgeBaseService.search()` 支持 hybrid/dense/sparse 模式，并返回带有分数的 `HybridSearchResult`。但该能力尚未对用户暴露。命中测试接口让用户在将 KB 连接到 Agent 之前验证检索质量。

## What Changes — 变更内容

- 为 KB API 添加搜索/测试端点：`POST /api/knowledge-bases/{id}/search`，接受查询和可选的 mode/limit 参数，返回带分数的分块及其元数据
- 添加预览端点以检查存储的分块：`GET /api/knowledge-bases/{id}/chunks`，返回带内容预览的分页分块列表
- 添加搜索模式比较：对同一查询在 dense/sparse/hybrid 模式下运行，并返回并列结果，以便用户比较排序质量
- 返回分块级别的详细信息，包括内容片段、分数、来源文档和每次命中的搜索模式

## Capabilities — 能力

### New Capabilities — 新增能力

- `kb-hit-testing`：知识库命中测试 — 带评分结果的搜索端点、分块预览、模式比较和质量指标

### Modified Capabilities — 修改的能力

- `hybrid-search`：向 `HybridSearchResult` 添加每个结果的分数分解（dense_score、sparse_score），以便在命中测试输出中透明显示

## Impact — 影响范围

- **API**：`src/hecate/api/management/knowledge.py` — 3 个新端点（search、chunks、compare）
- **Services**：`src/hecate/services/rag/service.py` — 暴露现有搜索并添加分块列表功能
- **Searcher**：`src/hecate/services/rag/searcher.py` — 按模式返回分数分解
- **Models**：无需新的 ORM 模型 — 分块存储在 Qdrant 中，而非 PostgreSQL
- **Dependencies**：无新的外部依赖
