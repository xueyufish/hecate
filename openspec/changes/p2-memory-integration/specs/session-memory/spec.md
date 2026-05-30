# Session Memory — 会话内记忆集成

## Overview

将已有的三层记忆服务（L1 工作记忆、L2 会话压缩、L3 用户记忆）接入 ConversationService，让 Agent 在每轮对话中具备完整的记忆能力。

## Requirements

### REQ-1: L1 工作记忆注入

- ConversationService 在每轮 `assemble()` 前调用 `WorkingMemoryService.list_blocks(agent_id)` 加载该 Agent 的所有记忆块
- 将块列表传入 `ContextAssembler.assemble(memory_blocks=...)` 
- Agent 可通过工具调用 `update_memory_block(label, content)` 更新记忆块

### REQ-2: L2 会话压缩

- ConversationService 在 `assemble()` 时检查当前消息 token 数
- 当 token 数超过 `compression_threshold`（默认 4000）时，调用 `CompressionPipeline.compress()` 压缩历史消息
- 压缩后的消息替换原始消息传入 LLM，原始消息保留在 DB
- 会话结束后可查询压缩历史（压缩级别、token 节省量）

### REQ-3: L3 用户记忆提取与检索

- Assistant 回复后，调用 `UserMemoryService.extract_facts(user_id, messages)` 从对话中提取新事实
- 提取后调用 `store_memory()` 持久化
- 下一轮对话时，调用 `retrieve_memories(user_id, query)` 获取相关用户记忆，注入上下文

### REQ-4: 记忆工具注册

- 注册 `update_memory_block` 工具到 Agent 工具列表（当 Agent 配置了工作记忆时）
- 注册 `search_user_memory` 工具（当用户启用了 L3 记忆时）

## Scenarios

### Scenario 1: 长对话自动压缩

```
Given Agent 有 20 轮对话历史（约 6000 tokens）
When 用户发送新消息
Then 系统检测到 token 数超过阈值
And 调用 CompressionPipeline 自动压缩历史
And 使用压缩后的上下文调用 LLM
And 原始消息保留在 DB 中
```

### Scenario 2: 跨会话记忆用户偏好

```
Given 用户在会话 A 中提到 "我喜欢用 Python"
When 系统提取并存储用户记忆 {fact: "用户喜欢 Python", category: "preference"}
And 用户在会话 B 中问 "帮我写个脚本"
Then 系统检索到用户偏好，注入上下文
And Agent 使用 Python 编写脚本
```

### Scenario 3: Agent 主动更新工作记忆

```
Given Agent 配置了工作记忆块 "current_task"
When Agent 在执行过程中发现任务变更
And Agent 调用 update_memory_block("current_task", "新的任务描述")
Then 工作记忆块更新
And 下一轮对话时 Agent 能读取到更新后的记忆
```
