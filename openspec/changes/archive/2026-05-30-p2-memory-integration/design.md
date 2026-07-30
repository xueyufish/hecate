## Context — 背景

Hecate 已有三层记忆服务代码但未接入对话流程：

- **L1 WorkingMemoryService**（`services/memory/working_memory.py`）：命名记忆块 CRUD，`inject_blocks()` 方法将 block 内容注入消息
- **L2 CompressionPipeline**（`services/memory/compression.py`）：三级压缩（snip → microcompact → autocompact），`compress()` 返回 `CompressionResult`
- **L3 UserMemoryService**（`services/memory/user_memory.py`）：用户事实存储 + 向量检索，`store_memory()` / `retrieve_memories()` / `extract_facts()`
- **ContextAssembler**（`services/context/assembler.py`）：已有 `memory_blocks` 和 `user_memories` 参数及注入逻辑
- **ConversationService**（`services/conversation.py`）：当前不调用任何记忆服务，纯无状态

数据模型已就绪：`MemoryBlockModel`（L1），`MemoryModel`（L3）。API 骨架已存在：`api/management/memory.py`。

## Goals / Non-Goals — 目标 / 非目标

**目标:**
- ConversationService 每轮自动将 L1 工作记忆注入上下文
- 对话历史超过阈值时自动触发 L2 压缩
- L3 用户记忆在对话完成/轮次结束时提取
- 提供记忆管理 REST API（CRUD blocks、查看用户记忆、压缩状态）
- 前端可查看和编辑工作记忆、浏览用户记忆

**非目标:**
- 不实现跨 Agent 记忆共享（P3 范围）
- 不实现记忆版本化或回滚
- 不实现自定义压缩策略配置（使用默认阈值）
- 不实现记忆导入/导出

## Decisions — 决策

### D1: 记忆注入时机

**决策**: 在 `ContextAssembler.assemble()` 中注入，`ConversationService` 在调用 assemble 前从 DB 加载记忆。

**理由**: ContextAssembler 已有 `memory_blocks` 和 `user_memories` 参数，无需修改组装器本身。ConversationService 是对话编排的唯一入口点，是记忆加载的正确位置。

### D2: L2 压缩触发策略

**决策**: 使用 token 计数阈值触发。当 `TokenCounter.count_messages(messages)` 超过 `compression_threshold`（默认 4000 token）时，在注入新消息前压缩。

**理由**: CompressionPipeline 已实现完整的二级压缩逻辑。基于 token 的触发比消息计数更精确。阈值可配置。

### D3: L3 用户记忆提取时机

**决策**: 在每个 Assistant 轮次后，调用 `UserMemoryService.extract_facts()` 提取新事实，异步存储。不阻塞响应。

**理由**: `extract_facts()` 已实现（基于 LLM 的关键信息提取）。异步避免增加延迟。每轮提取确保及时的记忆更新。

### D4: 记忆 API 路由

**决策**: 复用现有 `api/management/memory.py`，扩展以下端点：
- `GET /api/agents/{agent_id}/memory/blocks` — 列出工作记忆块
- `POST /api/agents/{agent_id}/memory/blocks` — 创建/更新 block
- `DELETE /api/agents/{agent_id}/memory/blocks/{block_id}` — 删除 block
- `GET /api/users/{user_id}/memories` — 列出用户记忆
- `GET /api/sessions/{session_id}/compression` — 查看压缩状态

**理由**: 记忆块绑定到 Agent（Agent 配置定义需要哪些块），用户记忆绑定到用户。匹配资源所有权关系。

### D5: 前端记忆面板

**决策**: 在 Agent 详情页面添加 Memory 标签页，显示工作记忆块列表（可编辑）和用户记忆列表（只读）。

**理由**: 与 Agent 配置器统一入口点，无需独立页面。

## Risks / Trade-offs — 风险与权衡

- **L3 提取成本**: 每轮额外一次 LLM 调用用于事实提取。缓解措施：仅在 Assistant 响应包含个人信息/偏好内容时触发（可跳过简单问答）。
- **压缩信息丢失**: Autocompact 摘要可能丢失细节。缓解措施：在 DB 中保留原始消息，仅在上下文中使用压缩版本。
- **记忆一致性**: 多个 session 同时更新同一用户记忆。缓解措施：使用 `updated_at` 时间戳，最后写入胜出（简单有效）。
