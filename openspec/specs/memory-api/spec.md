# Memory API — 记忆管理 REST API

## Overview

提供工作记忆块 CRUD、用户记忆查看/搜索、压缩状态查询的 REST API 端点。

## Requirements

### REQ-1: 工作记忆块 CRUD

- `GET /api/agents/{agent_id}/memory/blocks` — 列出 Agent 的所有工作记忆块
- `POST /api/agents/{agent_id}/memory/blocks` — 创建或更新记忆块（label + content）
- `PUT /api/agents/{agent_id}/memory/blocks/{block_id}` — 更新指定记忆块
- `DELETE /api/agents/{agent_id}/memory/blocks/{block_id}` — 删除记忆块

### REQ-2: 用户记忆查看与搜索

- `GET /api/users/{user_id}/memories` — 列出用户的所有记忆事实（支持分页）
- `GET /api/users/{user_id}/memories/search?q={query}` — 语义搜索用户记忆
- `DELETE /api/users/{user_id}/memories/{memory_id}` — 删除指定记忆

### REQ-3: 压缩状态查询

- `GET /api/sessions/{session_id}/compression` — 返回会话压缩历史（级别、token 节省、时间戳）

### REQ-4: 认证与权限

- 所有端点需要 API Key 认证（复用 `verify_api_key` 依赖）
- Agent 记忆块只有拥有该 Agent 的用户可访问
- 用户记忆只有该用户本人可访问

## Scenarios

### Scenario 1: 管理工作记忆块

```
Given 用户有 Agent "assistant" (agent_id=abc)
When POST /api/agents/abc/memory/blocks {"label": "current_task", "content": "写周报"}
Then 返回 201 + 创建的记忆块详情
And Agent 下次对话时能读取到该块
```

### Scenario 2: 搜索用户记忆

```
Given 用户有记忆 {fact: "喜欢 Python", category: "preference"}
When GET /api/users/{user_id}/memories/search?q=编程语言
Then 返回包含该记忆的结果列表
```

### Scenario 3: 查看压缩历史

```
Given 会话经过 3 次压缩
When GET /api/sessions/{session_id}/compression
Then 返回压缩记录列表 [{level: "snip", tokens_saved: 1200, timestamp: "..."}, ...]
```
