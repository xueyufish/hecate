## Context

Hecate P1 实现了简化版 L2 会话记忆——对话历史列表 + 超长截断。P2 需要完整的三级记忆系统：

- **L1 工作记忆**：上下文窗口中的命名区域，Agent 可读写（参考 Letta MemoryBlock）
- **L2 完整压缩**：三级压缩管道替代简单截断（参考 Claude Code 5 层压缩）
- **L3 用户记忆**：从对话中提取持久事实，跨会话检索（参考 Mem0）

**当前状态**：
- P1 对话历史存储在 `messages` 表，无压缩
- Context Engineering 已实现 Token 预算管理和三级降级（DROP→COMPRESS→EMERGENCY）
- pgvector 已通过 Qdrant 集成（L4 知识记忆），L3 可复用向量检索
- tiktoken 已集成用于 Token 计数

## Goals / Non-Goals

**Goals:**

1. 提供 L1 工作记忆（MemoryBlock CRUD + 上下文组装）
2. 提供 L2 完整压缩管道（snip → microcompact → autocompact）
3. 提供 L3 用户记忆（事实提取 + 向量存储 + 跨会话检索）

**Non-Goals:**

1. 不实现 L4 知识记忆增强（属于知识库增强变更）
2. 不实现 Consolidation Agent（属于 P3）
3. 不实现记忆隔离（多租户，属于 P3）
4. 不修改 P1 已有的简化截断逻辑（保持向后兼容）

## Decisions

### D1: MemoryBlock 存储在独立表，通过 agent_id 关联

**选择**：新增 `memory_blocks` 表

```
memory_blocks: id, agent_id, label, content, position, limit, created_at, updated_at
```

**理由**：
- MemoryBlock 是 Agent 级别的配置，不是会话级别的
- label 是命名标识（如 "persona"、"user_profile"），Agent 可通过工具读写
- position 控制在上下文中的组装顺序
- limit 控制该块的最大 Token 数

### D2: L2 压缩管道集成到 Context Engineering

**选择**：在 `ContextAssembler` 中集成压缩管道，替代 P1 的简单截断

```
P1: messages → 截断 → LLM
P2: messages → snip → microcompact → autocompact → LLM
```

**理由**：
- 复用已有的 Token 预算管理
- 压缩是上下文组装的一部分，不是独立服务
- 渐进式：snip（删除低价值）→ microcompact（合并连续）→ autocompact（LLM 摘要）

### D3: L3 用户记忆使用 PostgreSQL + pgvector

**选择**：新增 `memories` 表，使用 pgvector 存储 embedding

```
memories: id, content, scope(JSONB), memory_type, importance, access_count, embedding(vector), created_at
```

**理由**：
- 复用已有的 PostgreSQL 基础设施
- pgvector 支持向量检索，不需要额外的 Qdrant 集群
- scope 包含 user_id + agent_id + session_id，支持多级隔离

### D4: 事实提取使用 LLM 工具调用

**选择**：通过 LLM 工具调用从对话中提取事实，而不是规则引擎

**理由**：
- LLM 理解语义，能提取隐含的偏好和知识
- 工具调用格式标准化，易于集成
- 可配置提取策略（哪些对话需要提取）

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| MemoryBlock 增加上下文大小 | 设置 limit 限制每个块的 Token 数 |
| L2 压缩可能丢失关键信息 | 保留最近 N 轮不压缩，压缩前评估信息密度 |
| L3 事实提取增加 LLM 调用 | 异步提取，不阻塞主对话流 |
| pgvector 性能 | 为 embedding 列创建索引，限制检索范围 |
