## ADDED Requirements — 新增需求

### 需求：所有资源模型的工作区限定数据隔离
每个属于租户的资源模型应有一个 `workspace_id` UUID 列，带有指向 `WorkspaceModel.id` 的外键。这些模型的所有服务层查询应通过 `workspace_id` 进行过滤以防止跨租户数据访问。涉及的模型包括：ConversationModel、MessageModel、SessionModel、DocumentModel、EvidenceModel、CheckpointModel、BudgetSnapshotModel、WorkflowVersionModel、WorkflowRunModel、PromptVersionModel、EvaluationDatasetModel、EvaluationItemModel、EvaluationRunModel、EvaluationScoreModel

#### 场景：查询工作区内的对话
- **当** 调用 `ConversationService.list(db, workspace_id=ws_id)`
- **则** 仅返回 `workspace_id == ws_id` 的对话

#### 场景：查询工作区内的消息
- **当** 查询工作区 `ws_id` 中某对话的 `MessageModel` 行
- **则** 仅返回 `workspace_id == ws_id` 的消息

#### 场景：查询工作区内的会话
- **当** 列出工作区的会话
- **则** 仅返回 `workspace_id == ws_id` 的会话

#### 场景：查询工作区内的文档
- **当** 列出工作区 `ws_id` 中知识库的文档
- **则** 仅返回 `workspace_id == ws_id` 的文档

#### 场景：跨租户访问被拒绝
- **当** 认证为工作区 A 的请求尝试读取工作区 B 中的资源
- **则** 服务应返回空结果（空列表）或对单个资源查找抛出 404

#### 场景：创建资源继承工作区
- **当** 创建新资源（对话、消息、会话、文档等）
- **则** 资源 `workspace_id` 应从认证的 `AuthContext.workspace_id` 设置

### 需求：向量存储工作区负载过滤
所有向量存储适配器（Qdrant、Chroma）应在每次向量插入时在负载元数据中包含 `workspace_id`，并在每个搜索查询上应用强制 `workspace_id` 过滤条件

#### 场景：向量插入包含 workspace_id 负载
- **当** 文档块被嵌入并存储在向量存储中
- **则** 点负载应包括与知识库工作区匹配的 `workspace_id`

#### 场景：向量搜索按 workspace_id 过滤
- **当** 为某个工作区执行混合或密集搜索
- **则** 搜索查询应包括负载过滤器 `workspace_id == ws_id`

#### 场景：没有 workspace_id 的向量搜索优雅降级
- **当** 向量点缺少 `workspace_id` 负载字段（遗留数据）
- **则** 搜索应回退到 `knowledge_base_id` 过滤并记录警告

### 需求：带回填的 Alembic 迁移
单个 Alembic 迁移应向所有 14 张表添加 `workspace_id` 列并附带复合索引，通过父实体关系按拓扑顺序回填现有行，然后添加 NOT NULL 和 FK 约束

#### 场景：迁移添加列和索引
- **当** 运行 `alembic upgrade head`
- **则** 所有 14 张表都有新的 `workspace_id` UUID 列，带有 `idx_<table>_workspace` 复合索引

#### 场景：回填从父级填充 workspace_id
- **当** 迁移在现有数据库上运行
- **则** 现有行从其父实体的 `workspace_id` 获取 workspace_id

#### 场景：回填顺序尊重依赖关系
- **当** 迁移回填数据
- **则** 父模型（ConversationModel、SessionModel、EvaluationDatasetModel 等）在子模型（MessageModel、EvidenceModel、EvaluationItemModel 等）之前回填

#### 场景：孤立行的默认工作区
- **当** 一行没有可解析的父实体（孤立数据）
- **则** 其 `workspace_id` 应设置为零 UUID 默认工作区
