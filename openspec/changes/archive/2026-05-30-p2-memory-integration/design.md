## Context

Hecate 已有三层记忆服务代码但未接入对话流程：

- **L1 WorkingMemoryService** (`services/memory/working_memory.py`): 命名记忆块 CRUD，`inject_blocks()` 方法将块内容注入 messages
- **L2 CompressionPipeline** (`services/memory/compression.py`): 三级压缩（snip → microcompact → autocompact），`compress()` 方法返回 `CompressionResult`
- **L3 UserMemoryService** (`services/memory/user_memory.py`): 用户事实存储 + 向量检索，`store_memory()` / `retrieve_memories()` / `extract_facts()`
- **ContextAssembler** (`services/context/assembler.py`): 已预留 `memory_blocks` 和 `user_memories` 参数及注入逻辑
- **ConversationService** (`services/conversation.py`): 当前不调用任何记忆服务，纯无状态

数据模型已就绪：`MemoryBlockModel`（L1）、`MemoryModel`（L3）。API 骨架存在：`api/management/memory.py`。

## Goals / Non-Goals

**Goals:**
- ConversationService 每轮对话自动注入 L1 工作记忆到上下文
- 对话历史超阈值时自动触发 L2 压缩
- 对话结束/轮次完成后自动提取 L3 用户记忆
- 提供记忆管理 REST API（CRUD 块、查看用户记忆、压缩状态）
- 前端可查看和编辑工作记忆、浏览用户记忆

**Non-Goals:**
- 不实现记忆的跨 Agent 共享（P3 范围）
- 不实现记忆版本控制或回滚
- 不实现自定义压缩策略配置（使用默认阈值）
- 不实现记忆的导入/导出

## Decisions

### D1: 记忆注入时机

**决策**: 在 `ContextAssembler.assemble()` 中注入，由 `ConversationService` 在调用 assemble 前从 DB 加载记忆。

**理由**: ContextAssembler 已有 `memory_blocks` 和 `user_memories` 参数，无需修改 assembler 本身。ConversationService 是对话编排的唯一入口，适合在此做记忆加载。

### D2: L2 压缩触发策略

**决策**: 使用 token 计数阈值触发。当 `TokenCounter.count_messages(messages)` 超过 `compression_threshold`（默认 4000 tokens）时，先压缩再注入新消息。

**理由**: CompressionPipeline 已实现完整的三级压缩逻辑。基于 token 计数而非消息数更精确。阈值可配置。

### D3: L3 用户记忆提取时机

**决策**: 在每轮 Assistant 回复后，调用 `UserMemoryService.extract_facts()` 提取新事实，异步存储。不阻塞响应返回。

**理由**: `extract_facts()` 已实现（基于 LLM 提取关键信息）。异步避免增加延迟。每轮提取确保记忆及时更新。

### D4: 记忆 API 路由

**决策**: 复用已有 `api/management/memory.py`，扩展为：
- `GET /api/agents/{agent_id}/memory/blocks` — 列出工作记忆块
- `POST /api/agents/{agent_id}/memory/blocks` — 创建/更新块
- `DELETE /api/agents/{agent_id}/memory/blocks/{block_id}` — 删除块
- `GET /api/users/{user_id}/memories` — 列出用户记忆
- `GET /api/sessions/{session_id}/compression` — 查看压缩状态

**理由**: 记忆块绑定到 Agent（Agent 配置需要哪些块），用户记忆绑定到用户。符合资源归属关系。

### D5: 前端记忆面板

**决策**: 在 Agent 详情页新增 Memory tab，展示工作记忆块列表（可编辑）和用户记忆列表（只读）。

**理由**: 与 Agent 配置器统一入口，不需要独立页面。

## Risks / Trade-offs

- **L3 提取成本**: 每轮对话额外一次 LLM 调用提取事实。缓解：仅当 Assistant 回复包含个人信息/偏好相关内容时触发（可跳过简单问答）。
- **压缩信息丢失**: autocompact 生成摘要可能丢失细节。缓解：保留原始消息在 DB，仅上下文中使用压缩版本。
- **记忆一致性**: 高并发场景下多个会话同时更新同一用户记忆。缓解：使用 `updated_at` 时间戳，后写覆盖（简单有效）。
