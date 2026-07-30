## ADDED Requirements — 新增的需求

### Requirement: Workspace Isolation on Memory Models — 需求：内存模型上的工作空间隔离

所有内存模型（L1 `MemoryBlockModel`、L3 `MemoryModel`、L4 `KnowledgeMemoryModel`）应有一个 `workspace_id` UUID 列作为一等字段。所有服务层查询应按 `workspace_id` 过滤以执行租户隔离。

#### Scenario: Query L1 memory blocks with workspace filter — 场景：带工作空间过滤器查询 L1 内存块
- **WHEN — 当** 调用 `WorkingMemoryService.list_blocks(agent_id, workspace_id=ws_id)`
- **THEN — 则** 仅返回 `workspace_id == ws_id` 且 `agent_id == agent_id` 的块

#### Scenario: Query L3 user memories with workspace filter — 场景：带工作空间过滤器查询 L3 用户内存
- **WHEN — 当** 调用 `UserMemoryService.retrieve_memories(query, scope, workspace_id=ws_id)`
- **THEN — 则** 仅在 `workspace_id == ws_id` 时返回内存，此外还有任何范围过滤器

#### Scenario: Query L4 knowledge with workspace filter — 场景：带工作空间过滤器查询 L4 知识
- **WHEN — 当** 调用 `KnowledgeMemoryService.search(query, agent_id, workspace_id=ws_id)`
- **THEN — 则** Qdrant 搜索负载过滤器包含 `workspace_id == ws_id`

#### Scenario: Create memory block with workspace — 场景：创建工作空间的内存块
- **WHEN — 当** 创建新的 `MemoryBlockModel`
- **THEN — 则** `workspace_id` 从智能体的工作空间设置（根据认证上下文验证）

#### Scenario: Create user memory with workspace — 场景：创建工作空间的用户内存
- **WHEN — 当** 创建新的 `MemoryModel`
- **THEN — 则** `workspace_id` 从请求认证上下文设置

### Requirement: Alembic Migration for workspace_id — 需求：workspace_id 的 Alembic 迁移

Alembic 迁移应向 `memory_blocks` 和 `memories` 表添加 `workspace_id` 列，服务器默认值为 `UUID('00000000-0000-0000-0000-000000000000')`，并创建复合索引 `(workspace_id, deleted)`。

#### Scenario: Migration adds workspace_id to memory_blocks — 场景：迁移向 memory_blocks 添加 workspace_id
- **WHEN — 当** 运行 `alembic upgrade head`
- **THEN — 则** `memory_blocks` 表具有新的 `workspace_id` UUID 列，索引为 `idx_memory_blocks_workspace`

#### Scenario: Migration adds workspace_id to memories — 场景：迁移向 memories 添加 workspace_id
- **WHEN — 当** 运行 `alembic upgrade head`
- **THEN — 则** `memories` 表具有新的 `workspace_id` UUID 列，索引为 `idx_memories_workspace`

#### Scenario: Existing rows get default workspace — 场景：现有行获得默认工作空间
- **WHEN — 当** 迁移在现有数据库上运行
- **THEN — 则** `memory_blocks` 和 `memories` 中的所有现有行的 `workspace_id` 设置为零 UUID

### Requirement: API Workspace Context — 需求：API 工作空间上下文

所有内存 API 端点应从认证中间件接受工作空间上下文。`workspace_id` 应根据认证用户的允许工作空间进行验证。

#### Scenario: Memory block endpoints workspace enforcement — 场景：内存块端点工作空间执行
- **WHEN — 当** 调用 `POST /api/agents/{agent_id}/memory-blocks`
- **THEN — 则** 智能体的 `workspace_id` 用作工作空间上下文，创建的块继承它

#### Scenario: User memory endpoints workspace enforcement — 场景：用户内存端点工作空间执行
- **WHEN — 当** 调用 `GET /api/users/{user_id}/memories`
- **THEN — 则** 仅返回经过认证工作空间内的内存
