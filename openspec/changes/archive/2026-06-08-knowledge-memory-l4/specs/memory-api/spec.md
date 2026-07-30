## MODIFIED Requirements — 修改的需求

### Requirement: L4 Knowledge Memory Endpoints (ADDED) — 需求：L4 知识内存端点（新增）

系统应在 `/api/agents/{agent_id}/knowledge` 下提供用于 L4 知识内存管理的 REST API 端点。

#### Scenario: List knowledge memories — 场景：列出知识内存
- **WHEN — 当** 调用 `GET /api/agents/{agent_id}/knowledge`，带可选 `?tags=policy&limit=20&offset=0`
- **THEN — 则** 返回经过认证工作空间内智能体的分页知识内存列表

#### Scenario: Search knowledge memories — 场景：搜索知识内存
- **WHEN — 当** 调用 `POST /api/agents/{agent_id}/knowledge/search`，包含 `{"query": "...", "top_k": 5, "tags": ["policy"], "mode": "hybrid"}`
- **THEN — 则** 从 Qdrant 混合搜索返回评分搜索结果

#### Scenario: Create knowledge memory — 场景：创建知识内存
- **WHEN — 当** 调用 `POST /api/agents/{agent_id}/knowledge`，包含 `{"content": "...", "tags": [...], "importance": 0.8}`
- **THEN — 则** 创建带嵌入的知识内存，存储在 PostgreSQL + Qdrant 中，返回 201

#### Scenario: Delete knowledge memory — 场景：删除知识内存
- **WHEN — 当** 调用 `DELETE /api/agents/{agent_id}/knowledge/{memory_id}`
- **THEN — 则** 在 PostgreSQL 中软删除，从 Qdrant 移除，返回 204

### Requirement: Workspace Context on Existing Endpoints (MODIFIED) — 需求：现有端点上的工作空间上下文（修改）

所有现有的内存 API 端点现在应执行工作空间隔离。`workspace_id` 从智能体的工作空间（对于 L1 端点）或从认证上下文（对于 L3 端点）解析。

#### Scenario: List memory blocks with workspace filter — 场景：带工作空间过滤器列出内存块
- **WHEN — 当** 调用 `GET /api/agents/{agent_id}/memory-blocks`
- **THEN — 则** 仅返回 `workspace_id` 匹配智能体工作空间的块

#### Scenario: List user memories with workspace filter — 场景：带工作空间过滤器列出用户内存
- **WHEN — 当** 调用 `GET /api/users/{user_id}/memories`
- **THEN — 则** 仅返回经过认证工作空间内的内存

#### Scenario: Search user memories with workspace filter — 场景：带工作空间过滤器搜索用户内存
- **WHEN — 当** 调用 `GET /api/users/{user_id}/memories/search?q={query}`
- **THEN — 则** 仅搜索经过认证工作空间内的内存
