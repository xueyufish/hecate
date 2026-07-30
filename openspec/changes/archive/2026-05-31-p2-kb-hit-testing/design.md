## Context — 背景

Hecate 的 RAG 流水线已通过混合搜索（3.2.2 + 3.2.3）完成。服务层拥有：

- **`KnowledgeBaseService.search()`** — 接受 collection_name、query、limit、mode；返回 `list[HybridSearchResult]`
- **`HybridSearchResult`** — `id`、`score`、`content`、`metadata`、`sparse_score`
- **`HybridSearcher`** — 通过 Qdrant 原生融合支持 `"hybrid"`、`"dense"`、`"sparse"` 模式
- **`KnowledgeBaseModel`** — 拥有 `qdrant_collection`（映射 kb_id → 集合名称）、`search_mode`、`sparse_weight`
- **KB API**（`api/management/knowledge.py`）— 支持 CRUD + 文档上传，但**无搜索端点**

分块存储在 Qdrant 中（而非 PostgreSQL）。每个点都有一个向量、可选的稀疏向量以及包含 `content`、`metadata`（包括来源文档信息）的载荷。`QdrantIndexer` 提供 `search()`、`search_sparse()`、`search_hybrid()` 方法。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 暴露 KB 命中测试的搜索端点：用户发送查询，获得带内容片段的评分结果
- 提供分块浏览：已存储分块的分页列表，以便用户验证数据摄入质量
- 模式比较：在 dense/sparse/hybrid 上运行同一查询并返回并列结果
- 返回可操作细节：分数、内容片段、来源文档、dense/sparse 分数分解

**非目标：**
- 不新增 ORM 模型 — 分块存在 Qdrant 中，不在 PostgreSQL 中
- 不提供分块编辑或删除 UI — 属于 KB 管理范畴，非命中测试
- 不提供自动质量评分或建议 — 用户自行解读结果
- 不提供重排序或交叉编码器 — 属于 P4（3.2.5）

## Decisions — 决策

### D1：搜索端点为 POST（而非 GET）

**决策**：`POST /api/knowledge-bases/{id}/search`，使用 JSON 体 `{query, mode, limit}`。

**理由**：搜索查询可能很长（完整句子、多行）。POST body 比 URL 编码的 GET 参数更自然。与代码库中 `POST /api/workflows/{id}/test-run` 的模式一致。

**考虑过的替代方案**：`GET /api/knowledge-bases/{id}/search?q=...&mode=...` — 因查询常常超出 URL 长度限制且含特殊字符而被拒绝。

### D2：通过 Qdrant scroll（而非 PostgreSQL）列出分块

**决策**：`GET /api/knowledge-bases/{id}/chunks` 使用 Qdrant 的 `scroll()` API 对存储的点进行分页。分块数据不存入 PostgreSQL。

**理由**：分块仅存储在 Qdrant 载荷中。添加 PostgreSQL 镜像会冗余并引入同步问题。Qdrant 的 `scroll()` 原生支持游标分页。

**考虑过的替代方案**：将分块内容存储在 `DocumentChunkModel` 中 — 因会重复 Qdrant 数据并需要同步逻辑而被拒绝。

### D3：模式比较作为独立端点

**决策**：`POST /api/knowledge-bases/{id}/compare` 在所有 3 种模式（dense、sparse、hybrid）下运行查询，并返回包含每种模式结果的结构化响应。

**理由**：比较是与单模式搜索不同的用例。用户希望看到不同模式如何对同一查询排序。独立端点保持搜索端点简单、比较端点专注。

**考虑过的替代方案**：向搜索端点添加 `compare=true` 标志 — 因响应结构差异显著（3 组结果 vs 1 组）而被拒绝。

### D4：通过 `HybridSearchResult` 扩展实现分数分解

**决策**：扩展 `HybridSearchResult`，添加可选的 `dense_score` 和 `sparse_weight` 字段（不仅是 `sparse_score`）。使用 hybrid 模式时，两个单独分数都会被填充，以便用户了解每种模式的贡献。

**理由**：目前的 `sparse_score` 字段仅显示稀疏贡献。为实现命中测试透明，用户需要独立查看 dense 和 sparse 分数，以及最终融合分数。

## Risks / Trade-offs — 风险 / 权衡

- **[Qdrant scroll 性能]** — 滚动大型集合（10 万+ 分块）可能较慢。→ 缓解措施：限制 page_size（最大 50），使用游标分页。
- **[比较功能的搜索延迟]** — Compare 端点顺序执行 3 次搜索。→ 缓解措施：使用 `asyncio.gather()` 并行运行，每次限制较小的 limit（默认 5）。
- **[PostgreSQL 中无分块计数]** — 不查询 Qdrant 就无法查看总分块数。→ 缓解措施：使用 Qdrant 的 `count()` API 获取集合大小。
