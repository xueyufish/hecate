## Why

P1 的 L2 会话记忆是简化版——只有对话历史列表 + 超长截断。这导致：

1. **长对话质量下降**：截断丢失早期重要上下文，Agent "遗忘"关键决策
2. **无工作记忆**：Agent 无法主动编辑自己的上下文（如 Letta 的 MemoryBlock），system prompt 是静态的
3. **无跨会话记忆**：每次对话都是从零开始，Agent 不记得用户偏好、历史交互

P2 记忆系统是 Agent 质量的核心提升——让 Agent 具备"记性"。

## What Changes

- **L1 工作记忆**：新增 `MemoryBlockModel`，Agent 可在上下文窗口中拥有命名区域（persona、user_profile、domain_context），每轮可读写
- **L2 完整压缩**：升级会话记忆为三级压缩管道（snip → microcompact → autocompact），替代简单截断
- **L3 用户记忆**：新增 `MemoryModel`，从对话中提取持久事实/偏好/知识，跨会话检索

## Capabilities

### New Capabilities

- `working-memory`: L1 工作记忆——MemoryBlock CRUD、上下文组装、Agent 可编辑
- `conversation-compression`: L2 会话压缩——snip（截断低价值消息）、microcompact（压缩连续消息）、autocompact（LLM 摘要）
- `user-memory`: L3 用户记忆——事实提取、向量存储、跨会话检索、重要性评分

### Modified Capabilities

（无——所有变更都是新增模块，不修改现有 spec 行为）

## Impact

- **新增代码**：`models/memory.py`、`services/memory/`、`api/management/memory.py`，约 1200-1500 行
- **新增数据模型**：`MemoryBlockModel`、`MemoryModel`，Alembic migration
- **新增 API**：`/api/agents/{id}/memory-blocks`、`/api/memory` CRUD + 检索
- **修改代码**：`services/context/assembler.py`（集成 MemoryBlock 到上下文组装）、`services/conversation.py`（集成压缩管道）
- **新增依赖**：无（复用已有 tiktoken + pgvector）
- **零破坏性变更**：P1 的所有 API 接口、数据模型、引擎行为保持不变
