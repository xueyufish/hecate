## Context — 上下文

Hecate 已有 10 个带 `workspace_id` 的资源模型（agents、workflows、tools、knowledge bases、prompts、memory blocks、memories、knowledge memories、skills、API keys），通过组织-rbac 变更（10.1 + 10.2）中建立的 `AuthContext` → `workspace_id` 流程强制执行。然而，14 个模型完全缺少 `workspace_id`，导致对话、消息、会话、文档、证据、检查点、预算快照、工作流版本/运行、提示版本以及评估数据集/项/运行/得分没有租户边界。

当前认证流程：
```
Request → Bearer token → get_auth_context() → AuthContext(workspace_id) → service.workspace_id filter
```

此流程适用于 10 个已限定范围的模型，但打破了对 14 个未限定范围模型的处理——服务要么根本不进行过滤，要么依赖间接 JOIN（例如 `session → agent → workspace_id`），这是脆弱且不一致的。

向量存储（Qdrant、Chroma）存储嵌入而不含 `workspace_id` 负载字段。当前隔离完全依赖于知识库-工作区关系，这意味着直接向量存储查询会绕过租户边界。

## Goals / Non-Goals — 目标 / 非目标

**目标：**

- 向所有 14 个未限定范围的资源模型添加 `workspace_id` FK
- 通过父实体关系用正确的 `workspace_id` 回填现有行
- 在所有向量存储插入中添加 `workspace_id` 负载，并在所有搜索查询中进行过滤
- 确保 14 个模型的每个服务查询都强制执行 `workspace_id`
- 每个 API 端点从 `AuthContext` 传递 `workspace_id` 到服务层

**非目标：**

- 计算隔离（每个工作区的沙箱）。延迟到 9.4c/9.4d
- 网络隔离（出口控制、域名白名单）。延迟到 9.7
- PostgreSQL 行级安全（RLS）。应用级过滤已经足够
- 自动查询拦截器（SQLAlchemy 会话事件或中间件）。手动服务级过滤保持模式明确且可审计
- `ModelProviderModel` / `ModelRegistryModel`——这些有意保持全局（所有工作区共享模型配置）
- `UserModel` / `OrganizationModel`——跨租户设计

## Decisions — 决策

### D1：在所有模型上使用直接 workspace_id FK（而非间接 JOIN）

**决策**：在所有 14 个模型上添加 `workspace_id` 作为直接 FK 列，包括高流量表（ConversationModel、MessageModel、SessionModel）

**考虑的替代方案**：
- **通过 agent_id JOIN 间接引用**：每个查询需要 2-3 次 JOIN 才能到达 `workspace_id`。每次聊天请求的 JOIN 开销是永久性的，对系统中最热的表不可接受
- **SQLAlchemy 查询拦截器**：通过会话事件自动注入 workspace。引入隐形魔法，增加调试难度。违反项目"显式优于隐式"的约定

**理由**：直接 FK 提供每行的 O(1) workspace 查找、无需 JOIN，以及数据永不跨租户泄漏的硬性数据库级保证。迁移成本是一次性的；查询性能收益是永久性的

### D2：通过父实体关系回填

**决策**：迁移通过关联到父实体的 `workspace_id` 来填充 `workspace_id`：

| 模型 | 父级 → workspace_id 来源 |
|-------|------------------------------|
| ConversationModel | `agent_id → AgentModel.workspace_id` |
| MessageModel | `conversation_id → ConversationModel.agent_id → AgentModel.workspace_id`（在 ConversationModel 回填后） |
| SessionModel | `agent_id → AgentModel.workspace_id` |
| DocumentModel | `knowledge_base_id → KnowledgeBaseModel.workspace_id` |
| EvidenceModel | `session_id → SessionModel.agent_id → AgentModel.workspace_id`（在 SessionModel 回填后） |
| CheckpointModel | `session_id → SessionModel.agent_id → AgentModel.workspace_id`（在 SessionModel 回填后） |
| BudgetSnapshotModel | `session_id → SessionModel.agent_id → AgentModel.workspace_id`（在 SessionModel 回填后） |
| WorkflowVersionModel | `workflow_id → WorkflowModel.workspace_id` |
| WorkflowRunModel | `workflow_id → WorkflowModel.workspace_id` |
| PromptVersionModel | `prompt_id → PromptModel.workspace_id` |
| EvaluationDatasetModel | `agent_id → AgentModel.workspace_id` |
| EvaluationItemModel | `dataset_id → EvaluationDatasetModel.workspace_id`（在 EvaluationDatasetModel 回填后） |
| EvaluationRunModel | `dataset_id → EvaluationDatasetModel.workspace_id`（在 EvaluationDatasetModel 回填后） |
| EvaluationScoreModel | `run_id → EvaluationRunModel.workspace_id`（在 EvaluationRunModel 回填后） |

**理由**：每个模型都有清晰的父级链到工作区限定实体。回填顺序遵循依赖深度（父级优先，子级其次）

### D3：使用 workspace_id 的向量数据库负载过滤

**决策**：在每次 upsert 时将 `workspace_id` 添加到向量存储负载，并在每个搜索查询中将其作为强制过滤条件

**考虑的替代方案**：
- **每个工作区独立集合**：1000 个工作区 = 1000 个集合。集合扩散、管理开销，且跨工作区搜索变得不可能
- **仅按知识库集合（当前）**：间接隔离有效但缺乏纵深防御。`knowledge_base_id` 过滤中的错误会暴露跨租户向量

**理由**：负载过滤是向量数据库中多租户的标准模式（Qdrant 推荐）。一个过滤条件的开销可以忽略不计。对 `knowledge_base_id` 绕过的纵深防御

### D4：在 (workspace_id, deleted) 上创建复合索引

**决策**：在所有 14 张表上添加 `Index("idx_<table>_workspace", "workspace_id", "deleted")`，匹配 10 个已限定范围模型上的现有索引模式

**理由**：与现有索引命名约定一致。复合索引支持最常见的查询模式：`WHERE workspace_id = :ws AND deleted = false`

## Risks / Trade-offs — 风险 / 权衡

**[R1] 涉及 14 张表的大规模迁移** → 迁移在潜在数百万行（对话、消息）上运行 UPDATE。**缓解措施**：批量更新（在循环中使用 `UPDATE ... LIMIT 10000`）以避免长时间锁表。在低流量窗口期间执行

**[R2] 回填顺序依赖** → 某些模型依赖其他模型先被回填（例如 MessageModel 需要 ConversationModel）。**缓解措施**：迁移按拓扑顺序运行回填（父级在子级之前）。包装在具有显式排序的事务中

**[R3] 向量存储回填** → 现有向量缺少 `workspace_id` 负载。**缓解措施**：通过 Qdrant/Chroma scroll + update API 进行批量负载更新。优雅降级：如果负载缺失，回退到 `knowledge_base_id` 过滤（当前行为）

**[R4] 服务方法签名变更** → 14 个模型的所有服务方法增加 `workspace_id` 参数。**缓解措施**：`workspace_id` 默认为 `None` → 零 UUID，匹配现有模式。API 端点已传入 `AuthContext.workspace_id`

## Migration Plan — 迁移计划

1. **Phase 1 — Schema**：向 14 张表添加 `workspace_id` 列（先可为空，无 FK 约束）。添加复合索引
2. **Phase 2 — Backfill**：按拓扑顺序从父实体填充 `workspace_id`
3. **Phase 3 — Constraints**：添加 NOT NULL 约束 + 指向 `workspaces(id)` 的 FK。现有零 UUID 行引用引导默认工作区
4. **Phase 4 — Vector DB**：批量更新 Qdrant/Chroma 负载，添加 `workspace_id`
5. **Phase 5 — Code**：部署强制执行 `workspace_id` 过滤的服务/API 变更

**回滚**：迁移是可逆的——降级删除列和索引。向量存储负载更新是非破坏性的（仅追加）
