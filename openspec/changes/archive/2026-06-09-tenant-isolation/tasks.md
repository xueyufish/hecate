## 1. Schema — 向 14 个模型添加 workspace_id

- [x] 1.1 向 ConversationModel 添加 `workspace_id` 列 + FK + 复合索引（`src/hecate/models/conversation.py`）
- [x] 1.2 向 MessageModel 添加 `workspace_id` 列 + FK + 复合索引（`src/hecate/models/message.py`）
- [x] 1.3 向 SessionModel 添加 `workspace_id` 列 + FK + 复合索引（`src/hecate/models/session.py`）
- [x] 1.4 向 DocumentModel 添加 `workspace_id` 列 + FK + 复合索引（`src/hecate/models/document.py`）
- [x] 1.5 向 EvidenceModel 添加 `workspace_id` 列 + FK + 复合索引（`src/hecate/models/evidence.py`）
- [x] 1.6 向 CheckpointModel 添加 `workspace_id` 列 + FK + 复合索引（`src/hecate/models/checkpoint.py`）
- [x] 1.7 向 BudgetSnapshotModel 添加 `workspace_id` 列 + FK + 复合索引（`src/hecate/models/budget.py`）
- [x] 1.8 向 WorkflowVersionModel + WorkflowRunModel 添加 `workspace_id` 列 + FK + 复合索引（`src/hecate/models/workflow.py`）
- [x] 1.9 向 PromptVersionModel 添加 `workspace_id` 列 + FK + 复合索引（`src/hecate/models/prompt.py`）
- [x] 1.10 向 EvaluationDatasetModel + EvaluationItemModel + EvaluationRunModel + EvaluationScoreModel 添加 `workspace_id` 列 + FK + 复合索引（`src/hecate/models/evaluation.py`）
- [x] 1.11 更新所有 14 个模型的 Pydantic Create/Read 模式以包含 `workspace_id`

## 2. Alembic 迁移

- [x] 2.1 创建迁移 `013_tenant_isolation_workspace_id.py`——向所有 14 张表添加可空 `workspace_id` 列 + 复合索引
- [x] 2.2 实现拓扑回填：父模型优先（ConversationModel、SessionModel、DocumentModel、WorkflowVersionModel、PromptVersionModel、EvaluationDatasetModel），然后是子模型（MessageModel、EvidenceModel、CheckpointModel、BudgetSnapshotModel、WorkflowRunModel、EvaluationItemModel、EvaluationRunModel、EvaluationScoreModel）
- [x] 2.3 回填后添加 NOT NULL 约束 + 指向 `workspaces(id)` 的 FK。孤立行默认为零 UUID

## 3. 向量存储工作区过滤

- [x] 3.1 在 Qdrant 存储适配器 upsert 中添加 `workspace_id` 到负载（`src/hecate/services/rag/qdrant_store.py`）
- [x] 3.2 在 Qdrant 搜索查询中添加强制 `workspace_id` 过滤器（`src/hecate/services/rag/qdrant_store.py`）
- [x] 3.3 在 Chroma 存储适配器 upsert 中添加 `workspace_id` 到元数据（`src/hecate/services/rag/chroma_store.py`）
- [x] 3.4 在 Chroma 搜索查询中添加强制 `workspace_id` 过滤器（`src/hecate/services/rag/chroma_store.py`）
- [x] 3.5 更新 KnowledgeBaseService 传递 `workspace_id` 到向量存储操作（`src/hecate/services/rag/service.py`）
- [x] 3.6 更新 KnowledgeMemoryService 搜索以在 Qdrant 查询中包含 `workspace_id` 过滤器（`src/hecate/services/memory/knowledge_memory.py`）
- [x] 3.7 添加优雅降级：如果向量负载缺少 `workspace_id`，回退到 `knowledge_base_id` 过滤并记录警告

## 4. 服务层 — 工作区强制

- [x] 4.1 更新 ConversationService 在所有查询中接受并按 `workspace_id` 过滤（`src/hecate/services/conversation.py`）
- [x] 4.2 确保 SessionModel 查询从代理上下文继承 `workspace_id`（已通过 agent_id 间接引用——添加直接过滤）
- [x] 4.3 更新 WorkflowExecutionService 在创建工作流版本/运行时传递 `workspace_id`（`src/hecate/services/workflow/execution_service.py`）
- [x] 4.4 更新 EvaluationDatasetService 在所有查询中接受并按 `workspace_id` 过滤（`src/hecate/services/evaluation/dataset_service.py`）
- [x] 4.5 更新其余服务（EvidenceModel、CheckpointModel、BudgetSnapshotModel、PromptVersionModel）从父实体上下文传递 `workspace_id`

## 5. API 层 — 传递 AuthContext.workspace_id

- [x] 5.1 更新对话 API 端点以传递 `ctx.workspace_id` 到 ConversationService
- [x] 5.2 更新会话 API 端点以传递 `ctx.workspace_id` 进行会话查询
- [x] 5.3 更新工作流版本/运行 API 端点以传递 `ctx.workspace_id`
- [x] 5.4 更新评估 API 端点以传递 `ctx.workspace_id` 到 EvaluationDatasetService
- [x] 5.5 更新提示版本 API 端点以传递 `ctx.workspace_id`

## 6. 测试

- [x] 6.1 测试跨租户隔离：在工作区 A 中创建数据，验证工作区 B 无法访问——涵盖 ConversationModel、MessageModel、SessionModel、DocumentModel
- [x] 6.2 测试工作流版本/运行和评估模型的跨租户隔离
- [x] 6.3 测试向量存储 workspace_id 负载包含在 upsert 中并在搜索时强制执行
- [x] 6.4 测试迁移：全新安装产生正确模式，升级正确填充 workspace_id
- [x] 6.5 测试优雅降级：缺少 workspace_id 负载的向量搜索回退到 knowledge_base_id 过滤

## 7. 文档

- [x] 7.1 更新 `docs/features/feature-catalog.md`：将 10.5 租户隔离标记为 ✅，将范围限定为仅数据隔离
- [x] 7.2 更新 `docs/features/roadmap.md`：将 10.5 标记为 Sprint 4 完成，更新统计信息
