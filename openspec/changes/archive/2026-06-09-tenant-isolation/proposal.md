## Why — 原因

Hecate 已实现组织管理（10.1）和 RBAC（10.2），但 14 个资源模型仍然缺少 `workspace_id`——这意味着对话、消息、会话、文档、检查点、证据、工作流版本/运行、提示版本和评估数据没有租户边界。任何拥有有效 `agent_id` 的认证用户都可以查询跨租户数据。向量存储（Qdrant/Chroma）也缺少 `workspace_id` 负载过滤，仅依赖间接的知识库作用域。这是多租户数据层完成前的最后一个缺口。

## What Changes — 变更内容

- 向 14 个模型添加 `workspace_id` UUID FK 列：ConversationModel、MessageModel、SessionModel、DocumentModel、EvidenceModel、CheckpointModel、BudgetSnapshotModel、WorkflowVersionModel、WorkflowRunModel、PromptVersionModel、EvaluationDatasetModel、EvaluationItemModel、EvaluationRunModel、EvaluationScoreModel
- 通过父实体关系（例如 `conversation.workspace_id ← agent.workspace_id`）添加带回填的 Alembic 迁移
- 在所有 Qdrant/Chroma 向量插入中添加 `workspace_id` 负载字段，并在所有搜索查询中添加过滤
- 更新 14 个新限定模型的所有服务层查询，以按 `workspace_id` 过滤
- 更新功能目录，将 10.5 的范围限定为仅数据隔离（计算/网络延迟到 9.4c/9.4d/9.7）

## Capabilities — 能力

### New Capabilities — 新增能力
- `tenant-data-isolation`：所有资源模型和向量存储的工作区限定数据隔离——每个查询强制使用 workspace_id，每个向量负载包含 workspace_id

### Modified Capabilities — 修改的能力
- `data-models`：向 14 个资源模型添加 workspace_id FK（ConversationModel、MessageModel、SessionModel、DocumentModel、EvidenceModel、CheckpointModel、BudgetSnapshotModel、WorkflowVersionModel、WorkflowRunModel、PromptVersionModel、EvaluationDatasetModel、EvaluationItemModel、EvaluationRunModel、EvaluationScoreModel）
- `memory-isolation`：在 Qdrant/Chroma 向量存储操作中添加 workspace_id 负载过滤器（当前规范仅涵盖 SQL 级隔离，未涉及向量数据库）

## Impact — 影响

- **Models**：14 个 ORM 模型新增列 + 索引 + FK
- **Services**：ConversationService、WorkflowExecutionService、EvaluationDatasetService 以及所有涉及 14 个模型的服务必须添加 workspace_id 参数和过滤器
- **API**：所有涉及对话、消息、会话、文档、证据、检查点、工作流版本/运行、提示版本和评估数据的端点必须从 AuthContext 传递 workspace_id
- **Vector DB**：Qdrant 和 Chroma 存储适配器必须将 workspace_id 注入负载并在搜索时进行过滤
- **Migration**：单个 Alembic 迁移添加 14 个列并回填。现有行从父实体（agent/knowledge-base）获取 workspace_id
- **Tests**：针对所有受影响的模型和向量存储的跨租户隔离新测试（租户 A 不能读取租户 B 的数据）
