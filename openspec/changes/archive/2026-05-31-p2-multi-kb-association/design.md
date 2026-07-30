## Context — 背景

功能 3.2.7"多知识库关联"要求一个 Agent 可关联多个知识库。当前实现将 `knowledge_base_ids` 以 JSON 数组形式存储在 `AgentModel` 上 — 数据模型已支持 M:N 语义。RAG 流水线（`ConversationService._retrieve_knowledge()`）已经可以遍历多个 KB ID。Agent 配置器前端已经提供了多选的 `KnowledgeSelector` 组件。

然而，多个集成缺口阻碍了其达到生产就绪状态：

1. **无 KB 验证** — Agent 可能引用不存在的或已删除的 KB ID
2. **无级联清理** — 删除 KB 会在 Agent 记录中留下过期引用
3. **聊天未自动加载 KB** — 前端聊天页面未将 Agent 的 KB ID 传递给聊天端点
4. **无反向查找** — 无法查询"哪些 Agent 使用了这个 KB？"
5. **每个 KB 单独排序** — 当前搜索先取每个 KB 的 Top-N 再合并；跨 KB 全局排序更准确

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 在创建或更新 Agent 时验证 KB ID
- 删除 KB 时的级联清理
- 在聊天流程中自动加载 Agent KB ID（前端获取 Agent 配置，传递 `kb_ids`）
- 在聊天 UI 中显示活跃的 KB 指示器
- 反向查找 API，查找使用了特定 KB 的 Agent
- 跨 KB 搜索结果聚合，使用全局分数排序

**非目标：**
- 从 JSON 数组迁移到关联表（推迟 — JSON 数组在当前规模下已足够）
- 每个 Agent 的 KB 优先级/权重（未来增强）
- KB 访问控制或权限检查（P3 多租户关注点）
- 实时 KB 同步或 webhook 通知

## Decisions — 决策

### D1：保留 JSON 数组用于 Agent-KB 关系（不引入关联表）

**决策**：保留 `agents` 表上的 `knowledge_base_ids` JSON 列。添加应用层验证和级联清理。

**理由**：关联表可以提供引用完整性和更好的查询性能，但会引入：
- Alembic 迁移复杂度（从 JSON 迁移到关联表 + 数据回填）
- ORM 关系配置变更
- API 模式变更（内部表示偏离 API 合约）
- 可能破坏现有代码路径的风险

在当前规模（<1 万个 Agent、<100 个 KB）下，JSON 数组方法已足够。应用层验证可实现相同的完整性保证。如果规模增长，迁移到关联表可作为单独的变更处理。

**考虑过的替代方案**：
- 关联表 `agent_knowledge_bases(agent_id, kb_id, priority, created_at)` — 适合大规模场景，但对 P2 来说过度设计
- 混合方案：JSON 用于读取，关联表用于完整性 — 增加了复杂度但没有明显收益

### D2：通过 Agent CRUD 中的批量查询进行验证

**决策**：在创建或更新 Agent 时，使用单个 `SELECT ... WHERE id IN (...)` 查询 `knowledge_bases` 表验证所有 KB ID。

**理由**：单次查询高效。返回 400 错误，列出哪些 KB ID 无效。此操作在数据库写入之前的 API 层执行。

### D3：通过 KB 服务中的删除后钩子进行级联清理

**决策**：当软删除 KB（设置 `deleted_at`）时，执行清理查询：`UPDATE agents SET knowledge_base_ids = array_remove(knowledge_base_ids, :kb_id) WHERE :kb_id = ANY(knowledge_base_ids)`。对于 SQLite 测试，在应用层使用 JSON 操作。

**理由**：将清理逻辑保留在服务层（而非数据库触发器）。与 `BaseModel` 已使用的软删除模式兼容。

**考虑过的替代方案**：
- 数据库触发器 — 增加了数据库特定的逻辑，更难测试
- 后台任务 — 对同步操作来说过度设计
- 不做清理，让验证捕获过期引用 — 用户体验差，Agent 静默丢失 KB 上下文

### D4：前端从 Agent 配置自动加载 KB ID

**决策**：聊天页面获取 Agent 的配置（已因模型名称而执行此操作），并在 `/v1/chat/completions` 请求中包含 `knowledge_base_ids` 作为 `kb_ids`。

**理由**：最小化变更。聊天页面已通过 `GET /api/agents/{agent_id}` 获取 Agent 数据。只需提取 `knowledge_base_ids` 并传递即可。

### D5：通过自定义 SQL 查询实现反向查找

**决策**：添加 `GET /api/knowledge-bases/{id}/agents` 端点，查询 `WHERE knowledge_base_ids::jsonb @> :kb_id::jsonb`（PostgreSQL）或应用层过滤（SQLite）。

**理由**：用于管理的简单端点。PostgreSQL JSON 包含操作符高效。SQLite 回退扫描所有 Agent（对管理工具可接受）。

## Risks / Trade-offs — 风险 / 权衡

- **[JSON 列性能]** — 对于 <1 万个 Agent，JSON 列查找足够快。如果规模增长，在专用变更中迁移到关联表。
- **[KB 删除的竞态条件]** — KB 软删除和 Agent 清理不是原子的。存在短暂窗口，其间 Agent 可能引用已删除的 KB。缓解措施：下次更新时由验证捕获。
- **[SQLite vs PostgreSQL JSON 处理]** — 级联清理查询在 SQLite 和 PostgreSQL 之间不同。使用应用级清理作为测试环境的回退。
- **[长期存在的 Agent 配置中的过期引用]** — 如果 KB 在 Agent 创建和聊天之间被删除，聊天将记录警告并跳过已删除的 KB（`_retrieve_knowledge` 中已有的优雅处理）。
