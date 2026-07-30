## ADDED Requirements — 新增需求

### Requirement: KB ID validation on agent create/update — 需求：Agent 创建/更新时的 KB ID 验证
当使用 `knowledge_base_ids` 创建或更新 Agent 时，系统应验证每个 KB ID 引用了存在的且未删除的知识库。如果任何 KB ID 无效，系统应拒绝请求并返回 HTTP 400，在消息中列出无效的 ID。

#### Scenario: Create agent with valid KB IDs — 场景：使用有效的 KB ID 创建 Agent
- **WHEN** 用户使用 `knowledge_base_ids: ["kb-uuid-1", "kb-uuid-2"]` 创建 Agent，两个 KB 都存在且未被删除
- **THEN** 系统应接受请求，并使用指定的 KB ID 存储 Agent

#### Scenario: Create agent with non-existent KB ID — 场景：使用不存在的 KB ID 创建 Agent
- **WHEN** 用户使用 `knowledge_base_ids: ["kb-uuid-1", "non-existent-uuid"]` 创建 Agent
- **THEN** 系统应拒绝请求，返回 HTTP 400 和类似 `"Invalid knowledge_base_ids: non-existent-uuid not found"` 的消息

#### Scenario: Create agent with soft-deleted KB ID — 场景：使用已软删除的 KB ID 创建 Agent
- **WHEN** 用户使用 `knowledge_base_ids: ["deleted-kb-uuid"]` 创建 Agent，该 KB 的 `deleted_at` 已被设置
- **THEN** 系统应拒绝请求，返回 HTTP 400 并指示 KB 未找到

#### Scenario: Update agent with empty KB list — 场景：使用空 KB 列表更新 Agent
- **WHEN** 用户使用 `knowledge_base_ids: []` 更新 Agent
- **THEN** 系统应接受请求并清除 Agent 的 KB 关联

#### Scenario: Create agent without KB IDs — 场景：创建 Agent 时不指定 KB ID
- **WHEN** 用户创建 Agent 时未指定 `knowledge_base_ids`
- **THEN** 系统应接受请求，默认使用空列表 `[]`

### Requirement: Cascade cleanup on KB deletion — 需求：KB 删除时的级联清理
当知识库被软删除时，系统应将其 ID 从所有引用它的 Agent 的 `knowledge_base_ids` 数组中移除。清理应为同步操作，并在删除响应返回前完成。

#### Scenario: Delete KB referenced by multiple agents — 场景：删除被多个 Agent 引用的 KB
- **WHEN** ID 为 `kb-uuid` 的知识库被软删除，Agent A 和 B 的 `knowledge_base_ids` 中都包含 `kb-uuid`
- **THEN** Agent A 和 B 的 `knowledge_base_ids` 中都应移除 `kb-uuid`，删除响应应反映清理已完成

#### Scenario: Delete KB not referenced by any agent — 场景：删除未被任何 Agent 引用的 KB
- **WHEN** 知识库被软删除，且没有 Agent 引用它
- **THEN** 系统应正常完成删除，不报错

#### Scenario: Delete KB referenced by one agent — 场景：删除被一个 Agent 引用的 KB
- **WHEN** 知识库被软删除，Agent A 的 `knowledge_base_ids` 为 `[kb-1, kb-2, kb-deleted]`
- **THEN** Agent A 的 `knowledge_base_ids` 应变为 `[kb-1, kb-2]`

### Requirement: Reverse lookup — agents using a KB — 需求：反向查找 — 使用某 KB 的 Agent
系统应提供 `GET /api/knowledge-bases/{id}/agents` 端点，返回引用指定知识库的 Agent 的分页列表。

#### Scenario: Query agents for a KB — 场景：查询使用某 KB 的 Agent
- **WHEN** 用户请求 `GET /api/knowledge-bases/{kb-id}/agents`
- **THEN** 系统应返回 `{"items": [...], "total": N}`，其中 items 是 `knowledge_base_ids` 中包含 `kb-id` 的 Agent

#### Scenario: Query agents for a non-existent KB — 场景：查询不存在的 KB 的 Agent
- **WHEN** 用户请求 `GET /api/knowledge-bases/{non-existent-id}/agents`
- **THEN** 系统应返回 HTTP 404

#### Scenario: Paginated reverse lookup — 场景：分页反向查找
- **WHEN** 用户请求 `GET /api/knowledge-bases/{kb-id}/agents?page=2&page_size=10`
- **THEN** 系统应返回第二页的 10 个 Agent，并附带总数

### Requirement: Cross-KB search result aggregation — 需求：跨 KB 搜索结果聚合
当跨多个知识库搜索时，系统应并行执行搜索，聚合所有结果，按分数全局降序排序，并返回跨所有 KB 的 top-k 结果。

#### Scenario: Search across 3 KBs — 场景：跨 3 个 KB 搜索
- **WHEN** Agent 有 3 个关联的 KB，用户发送一条消息
- **THEN** 系统应并行搜索全部 3 个 KB，合并结果，按分数降序排序，并返回全局排名前 5 的分块

#### Scenario: One KB search fails — 场景：某个 KB 搜索失败
- **WHEN** 跨 3 个 KB 搜索时其中一个搜索抛出异常
- **THEN** 系统应记录该 KB 的错误，聚合其余 2 个 KB 的结果，并返回全局排名前 5 的分块

### Requirement: Chat auto-loads agent KB IDs — 需求：聊天自动加载 Agent KB ID
前端聊天页面应在加载对话时获取 Agent 的配置，提取 `knowledge_base_ids`，并在每个 `/v1/chat/completions` 请求中将其作为 `kb_ids` 传递。

#### Scenario: Chat with agent having 2 KBs — 场景：与有 2 个 KB 的 Agent 聊天
- **WHEN** 用户打开与 Agent A 的对话，Agent A 有 `knowledge_base_ids: ["kb-1", "kb-2"]`
- **THEN** 每条聊天消息应在请求中包含 `kb_ids: ["kb-1", "kb-2"]`，响应应包括来自两个 KB 的引用

#### Scenario: Chat with agent having no KBs — 场景：与没有 KB 的 Agent 聊天
- **WHEN** 用户打开与 Agent A 的对话，Agent A 有 `knowledge_base_ids: []`
- **THEN** 聊天消息不应包含 `kb_ids`，响应不应包含引用

### Requirement: Active KB indicators in chat UI — 需求：聊天 UI 中的活跃 KB 指示器
聊天页面应显示徽章或指示器，展示当前对话中哪些知识库处于活跃状态。每个指示器应显示 KB 名称。

#### Scenario: Chat with 2 active KBs — 场景：有 2 个活跃 KB 的聊天
- **WHEN** 用户与关联了 2 个 KB 的 Agent 聊天
- **THEN** 聊天 UI 应在聊天头部或输入区域附近显示 2 个徽章，展示 KB 名称

#### Scenario: Chat with no KBs — 场景：没有 KB 的聊天
- **WHEN** 用户与没有 KB 关联的 Agent 聊天
- **THEN** 聊天 UI 不应显示任何 KB 指示器
