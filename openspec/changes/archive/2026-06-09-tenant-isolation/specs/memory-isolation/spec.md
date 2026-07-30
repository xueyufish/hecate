## MODIFIED Requirements — 修改的需求

### REQ-1：内存模型的工作区隔离
所有内存模型（L1 `MemoryBlockModel`、L3 `MemoryModel`、L4 `KnowledgeMemoryModel`）应具有 `workspace_id` UUID 列作为一级字段。所有服务层查询应通过 `workspace_id` 进行过滤以强制执行租户隔离。

#### 场景：使用工作区过滤器查询 L1 内存块
- **当** 调用 `WorkingMemoryService.list_blocks(agent_id, workspace_id=ws_id)`
- **则** 仅返回 `workspace_id == ws_id` 且 `agent_id == agent_id` 的块

#### 场景：使用工作区过滤器查询 L3 用户内存
- **当** 调用 `UserMemoryService.retrieve_memories(query, scope, workspace_id=ws_id)`
- **则** 仅返回 `workspace_id == ws_id` 的内存，此外还有任何作用域过滤器

#### 场景：使用工作区过滤器查询 L4 知识
- **当** 调用 `KnowledgeMemoryService.search(query, agent_id, workspace_id=ws_id)`
- **则** Qdrant 搜索负载过滤器应包括 `workspace_id == ws_id` 作为强制过滤条件

#### 场景：创建工作区限定的内存块
- **当** 创建新的 `MemoryBlockModel`
- **则** `workspace_id` 从代理的工作区设置（根据认证上下文验证）

#### 场景：创建工作区限定的用户内存
- **当** 创建新的 `MemoryModel`
- **则** `workspace_id` 从请求认证上下文设置

#### 场景：向量存储负载包含 workspace_id
- **当** L4 知识内存的向量存储在 Qdrant 中
- **则** 点负载应包括与内存工作区匹配的 `workspace_id`，且搜索查询应通过它进行过滤

### REQ-3：API 工作区上下文
所有内存 API 端点应从认证中间件接受工作区上下文。`workspace_id` 应根据认证用户被允许的工作区进行验证。

#### 场景：内存块端点工作区强制
- **当** 调用 `POST /api/agents/{agent_id}/memory-blocks`
- **则** 使用代理的 `workspace_id` 作为工作区上下文，创建的块继承该值

#### 场景：用户内存端点工作区强制
- **当** 调用 `GET /api/users/{user_id}/memories`
- **则** 仅返回认证工作区内的内存

## ADDED Requirements — 新增需求

### 需求：所有插入的向量存储工作区负载
所有向量存储适配器（Qdrant、Chroma）在插入内存或知识库块的点时，应在负载元数据中包含 `workspace_id`

#### 场景：Qdrant upsert 包含 workspace_id
- **当** 向量点被 upsert 到 Qdrant
- **则** 负载应包含带有工作区 UUID 的 `workspace_id` 字段

#### 场景：Chroma upsert 包含 workspace_id
- **当** 向量点被添加到 Chroma
- **则** 元数据应包含带有工作区 UUID 的 `workspace_id` 字段
