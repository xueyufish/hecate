# Memory API — 记忆管理 REST API

## Overview — 概述

提供工作记忆块 CRUD、用户记忆查看/搜索和压缩状态查询的 REST API 端点。

## Requirements — 需求

### REQ-1: 工作记忆块 CRUD

- `GET /api/agents/{agent_id}/memory/blocks` — 列出 Agent 的所有工作记忆块
- `POST /api/agents/{agent_id}/memory/blocks` — 创建或更新记忆块（label + content）
- `PUT /api/agents/{agent_id}/memory/blocks/{block_id}` — 更新特定记忆块
- `DELETE /api/agents/{agent_id}/memory/blocks/{block_id}` — 删除记忆块

### REQ-2: 用户记忆查看和搜索

- `GET /api/users/{user_id}/memories` — 列出所有用户记忆事实（支持分页）
- `GET /api/users/{user_id}/memories/search?q={query}` — 语义搜索用户记忆
- `DELETE /api/users/{user_id}/memories/{memory_id}` — 删除特定记忆

### REQ-3: 压缩状态查询

- `GET /api/sessions/{session_id}/compression` — 返回会话压缩历史（级别、节省的 token、时间戳）

### REQ-4: 认证与授权

- 所有端点需要 API Key 认证（复用 `verify_api_key` 依赖）
- Agent 记忆块仅对 Agent 的所有者可访问
- 用户记忆仅对用户本人可访问

## Scenarios — 场景

### 场景 1: 管理工作记忆块

```
假设用户有 Agent "assistant"（agent_id=abc）
当 POST /api/agents/abc/memory/blocks {"label": "current_task", "content": "Write weekly report"}
则 返回 201 + 创建的记忆块详情
且 Agent 可以在下一轮对话中读取此块
```

### 场景 2: 搜索用户记忆

```
假设用户有一条记忆 {fact: "likes Python", category: "preference"}
当 GET /api/users/{user_id}/memories/search?q=programming language
则 返回包含该记忆的结果列表
```

### 场景 3: 查看压缩历史

```
假设会话经历了 3 次压缩
当 GET /api/sessions/{session_id}/compression
则 返回压缩记录列表 [{level: "snip", tokens_saved: 1200, timestamp: "..."}, ...]
```
