## ADDED Requirements — 新增需求

### Requirement: Knowledge base search endpoint for hit testing — 需求：用于命中测试的知识库搜索端点
系统应提供 `POST /api/knowledge-bases/{id}/search` 端点，接受包含 `query`（字符串，必填）、`mode`（字符串，可选，默认 `"hybrid"`，可选值 `"hybrid"` / `"dense"` / `"sparse"`）、`limit`（整数，可选，默认 10，最大 50）的 JSON 体，返回匹配查询的评分分块列表。

每个结果应包括：`id`（分块 ID）、`score`（最终相关性分数）、`content`（分块文本）、`metadata`（来源文档信息）、`dense_score`（语义相似度分数）、`sparse_score`（关键词匹配分数）。

#### Scenario: Search with hybrid mode — 场景：使用混合模式搜索
- **WHEN** `POST /api/knowledge-bases/{kb_id}/search` 携带 `{"query": "machine learning", "mode": "hybrid"}`
- **THEN** 返回 200 及 `{"results": [...], "query": "machine learning", "mode": "hybrid", "total": <count>}`，每个结果包含 `score`、`content`、`dense_score`、`sparse_score`

#### Scenario: Search with nonexistent KB — 场景：搜索不存在的 KB
- **WHEN** `POST /api/knowledge-bases/{nonexistent_id}/search`
- **THEN** 返回 404，错误码 `NOT_FOUND`

#### Scenario: Search with empty query — 场景：空查询搜索
- **WHEN** `POST /api/knowledge-bases/{kb_id}/search` 携带 `{"query": ""}`
- **THEN** 返回 422 及验证错误

### Requirement: Chunk browsing endpoint — 需求：分块浏览端点
系统应提供 `GET /api/knowledge-bases/{id}/chunks` 端点，返回 Qdrant 集合中存储分块的分页列表，每个分块包含 `id`、`content`（截断至 200 字符）和 `metadata`。

#### Scenario: Browse chunks with pagination — 场景：分页浏览分块
- **WHEN** `GET /api/knowledge-bases/{kb_id}/chunks?page=1&page_size=20`
- **THEN** 返回 200 及 `{"items": [...], "total": <count>}`，每项包含 `id`、`content_preview`、`metadata`

#### Scenario: Browse chunks of empty KB — 场景：浏览空 KB 的分块
- **WHEN** `GET /api/knowledge-bases/{kb_id}/chunks` 针对没有文档的 KB
- **THEN** 返回 200 及 `{"items": [], "total": 0}`

### Requirement: Search mode comparison endpoint — 需求：搜索模式比较端点
系统应提供 `POST /api/knowledge-bases/{id}/compare` 端点，在 dense、sparse 和 hybrid 模式下对同一查询运行搜索，在单个响应中返回每种模式的结果。

#### Scenario: Compare modes for a query — 场景：比较查询的多种模式
- **WHEN** `POST /api/knowledge-bases/{kb_id}/compare` 携带 `{"query": "API authentication"}`
- **THEN** 返回 200 及 `{"dense": {"results": [...]}, "sparse": {"results": [...]}, "hybrid": {"results": [...]}, "query": "API authentication"}`，每种模式最多 5 个结果

#### Scenario: Compare with custom limit — 场景：使用自定义限制比较
- **WHEN** `POST /api/knowledge-bases/{kb_id}/compare` 携带 `{"query": "test", "limit": 3}`
- **THEN** 每种模式最多返回 3 个结果

## MODIFIED Requirements — 修改的需求

### Requirement: HybridSearchResult includes per-mode score breakdown — 需求：HybridSearchResult 包含每种模式的分数分解
系统应在 `HybridSearchResult` 中包含 `dense_score` 和 `sparse_score` 字段，在执行混合搜索时使用各模式分数填充，以便用户了解每种模式对最终排序的贡献。

#### Scenario: Hybrid search returns both mode scores — 场景：混合搜索返回两种模式的分数
- **WHEN** 对查询执行混合搜索
- **THEN** 每个结果包含 `score`（融合）、`dense_score`（语义）、`sparse_score`（关键词）字段
