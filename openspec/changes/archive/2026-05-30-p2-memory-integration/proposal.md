## Why — 动机

Agent 对话没有记忆——每次轮转都是无状态调用，无法跨会话记住用户偏好，也无法在长对话中自动压缩历史。`services/memory/` 目录已有完整的三层记忆服务代码（L1 工作记忆、L2 对话压缩、L3 用户记忆），`ContextAssembler` 已有 `memory_blocks` 和 `user_memories` 注入接口，但 `ConversationService` 从未调用它们。需要将现有记忆服务接入对话流程，使 Agent 拥有完整的记忆能力。

## What Changes — 变更内容

- 将 `CompressionPipeline`（L2）接入 `ConversationService`，实现长对话自动压缩
- 将 `WorkingMemoryService`（L1）接入 `ContextAssembler`，使 Agent 每轮可读写命名记忆块
- 将 `UserMemoryService`（L3）接入对话流程，实现跨会话用户事实持久化
- 在对话结束时自动提取用户记忆（偏好、事实、关键信息）
- 添加记忆管理 API（CRUD 记忆块、查看用户记忆、手动触发压缩）
- 添加前端记忆面板（查看/编辑工作记忆、用户记忆列表）

## Capabilities — 能力

### New Capabilities — 新增能力

- `session-memory`: 会话内记忆集成——L1 工作记忆注入上下文 + L2 自动对话压缩 + L3 用户记忆提取和检索
- `memory-api`: 记忆管理 REST API——CRUD 工作记忆块、查看/搜索用户记忆、压缩状态查询

### Modified Capabilities — 修改的能力

- （无现有 spec 需要修改）

## Impact — 影响

- **服务层**: `ConversationService` 增加记忆调用逻辑（压缩、提取、注入）
- **上下文层**: `ContextAssembler` 的 `memory_blocks` / `user_memories` 参数将真正被填充
- **API 层**: `api/management/memory.py` 已有骨架，需要扩展端点
- **数据层**: `MemoryBlockModel`, `MemoryModel` 已存在，需要确认 Alembic 迁移
- **依赖**: 无新增外部依赖，全部基于现有代码
