# Session Memory — 会话内记忆集成

## Overview — 概述

将现有的三层记忆服务（L1 工作记忆、L2 对话压缩、L3 用户记忆）接入 ConversationService，使 Agent 在每次对话轮次中都拥有完整的记忆能力。

## Requirements — 需求

### REQ-1: L1 工作记忆注入

- ConversationService 在每次 `assemble()` 前调用 `WorkingMemoryService.list_blocks(agent_id)` 加载该 Agent 的所有记忆块
- 将块列表传递给 `ContextAssembler.assemble(memory_blocks=...)`
- Agent 可通过 `update_memory_block(label, content)` 工具更新记忆块

### REQ-2: L2 对话压缩

- ConversationService 在 `assemble()` 期间检查当前消息的 token 数
- 当 token 数超过 `compression_threshold`（默认 4000）时，调用 `CompressionPipeline.compress()` 压缩历史
- 压缩后的消息在发送给 LLM 时替换原始消息；原始消息保留在 DB 中
- 压缩历史可在会话结束后查询（压缩级别、节省的 token）

### REQ-3: L3 用户记忆提取和检索

- 在 Assistant 响应后，调用 `UserMemoryService.extract_facts(user_id, messages)` 从对话中提取新事实
- 调用 `store_memory()` 持久化提取的事实
- 在下一轮中，调用 `retrieve_memories(user_id, query)` 获取相关用户记忆并注入上下文

### REQ-4: 记忆工具注册

- 在 Agent 工具列表中注册 `update_memory_block` 工具（当 Agent 配置了工作记忆时）
- 注册 `search_user_memory` 工具（当用户启用了 L3 记忆时）

## Scenarios — 场景

### 场景 1: 长对话自动压缩

```
假设 Agent 有 20 轮对话历史（~6000 tokens）
当 用户发送新消息
则 系统检测到 token 数超过阈值
且 调用 CompressionPipeline 自动压缩历史
且 使用压缩后的上下文调用 LLM
且 原始消息保留在 DB 中
```

### 场景 2: 跨会话用户偏好记忆

```
假设 用户在会话 A 中提到"我喜欢用 Python"
当 系统提取并存储用户记忆 {fact: "User likes Python", category: "preference"}
且 用户在会话 B 中问"帮我写个脚本"
则 系统检索用户偏好，注入上下文
且 Agent 用 Python 编写脚本
```

### 场景 3: Agent 主动更新工作记忆

```
假设 Agent 有工作记忆块 "current_task"
当 Agent 在执行期间检测到任务变更
且 Agent 调用 update_memory_block("current_task", "new task description")
则 工作记忆块被更新
且 Agent 可在下一轮读取更新后的记忆
```
