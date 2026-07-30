## 新增需求

### 需求：创建记忆块
系统须提供 API 端点 `POST /api/agents/{agent_id}/memory-blocks`，为 agent 创建新的记忆块。

#### 场景：成功创建
- **当** 用户发送带有 label, content, position 和 limit 的 POST 请求
- **则** 系统创建 MemoryBlockModel 并返回 201 及 block 数据

#### 场景：重复标签
- **当** 用户发送 POST 请求，但标签在该 agent 下已存在
- **则** 系统返回 409 Conflict

### 需求：读取记忆块
系统须提供 API 端点 `GET /api/agents/{agent_id}/memory-blocks/{block_id}`，返回记忆块。

#### 场景：Block 存在
- **当** 用户使用有效的 agent_id 和 block_id 发送 GET 请求
- **则** 系统返回 200 及 block 数据

#### 场景：Block 不存在
- **当** 用户对不存在的 block 发送 GET 请求
- **则** 系统返回 404

### 需求：更新记忆块
系统须提供 API 端点 `PUT /api/agents/{agent_id}/memory-blocks/{block_id}`，更新记忆块的内容。

#### 场景：成功更新
- **当** 用户发送包含更新内容的 PUT 请求
- **则** 系统更新 block 并返回 200

### 需求：删除记忆块
系统须提供 API 端点 `DELETE /api/agents/{agent_id}/memory-blocks/{block_id}`，删除记忆块。

#### 场景：成功删除
- **当** 用户发送 DELETE 请求
- **则** 系统删除 block 并返回 204

### 需求：列出记忆块
系统须提供 API 端点 `GET /api/agents/{agent_id}/memory-blocks`，返回 agent 的所有记忆块。

#### 场景：列出 blocks
- **当** 用户发送 GET 请求
- **则** 系统返回 200 及按 position 排序的所有 blocks

### 需求：上下文组装中的记忆块
系统须在构建 LLM prompt 时，将记忆块纳入上下文组装流程。

#### 场景：Blocks 包含在上下文中
- **当** 为具有记忆块的 agent 组装上下文
- **则** blocks 须按其配置的 position 插入到 messages 数组中，并遵守其 token 限制

#### 场景：未配置 blocks
- **当** agent 没有记忆块
- **则** 上下文组装须正常进行，不包含记忆块
