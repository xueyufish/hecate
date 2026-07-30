## 新增需求

### 需求：Agent 配置表单
系统须提供用于创建和编辑 Agent 的 UI 表单。表单须包含 3 个部分：基本信息（名称、描述、persona）、模型配置（model_name、temperature、max_tokens）和工具/知识库选择。

#### 场景：创建新 Agent
- **当** 用户打开 `/agents/new` 路由
- **则** 显示空表单，包含 3 个部分：基本信息、模型配置、工具/知识库
- **当** 用户填写必填字段并点击"创建"
- **则** 系统调用 `POST /api/agents` 并重定向到 agent 列表页面

#### 场景：编辑已有 Agent
- **当** 用户打开 `/agents/{id}/edit` 路由
- **则** 表单预填充现有 agent 数据（从 `GET /api/agents/{id}` 加载）
- **当** 用户修改字段并点击"保存"
- **则** 系统调用 `PATCH /api/agents/{id}` 并显示成功通知

#### 场景：必填字段验证
- **当** 用户提交表单时名称或 persona 字段为空
- **则** 表单显示内联验证错误，且不提交

### 需求：PATCH API 用于部分 Agent 更新
系统须暴露 `PATCH /api/agents/{id}` 端点，接受 agent 数据的 JSON body。仅更新请求中提供的字段。

#### 场景：部分更新仅名称
- **当** 调用 `PATCH /api/agents/{id}` 且 body 为 `{"name": "new-name"}`
- **则** 仅更新 agent 的名称，其他字段保持不变
- **则** 响应包含更新后 agent 的所有字段

#### 场景：完全更新
- **当** 调用 `PATCH /api/agents/{id}` 且 body 包含所有 agent 字段
- **则** 更新所有提供的字段

#### 场景：Agent 未找到
- **当** 调用 `PATCH /api/agents/nonexistent`
- **则** 响应为 404，错误码 `NOT_FOUND`

### 需求：Agent 复制
系统须允许用户通过复制现有 agent 的配置来创建新 agent。复制操作须将所有字段复制到新 agent，appended agent 名称添加"Copy"后缀。

#### 场景：从现有 Agent 复制
- **当** 用户在编辑页面上点击"复制"操作
- **则** 新表单预填充原始 agent 的所有字段，名称修改为 `{original_name} (Copy)`

### 需求：内联 Agent 测试面板
系统须在 Agent 配置页面上提供内联测试面板。测试面板须允许用户在保存更改前发送消息并查看 agent 响应。

#### 场景：内联测试面板
- **当** 用户在 agent 编辑页面上
- **则** 可折叠的测试面板位于表单下方，包含消息输入和对话显示
- **当** 用户输入消息并发送
- **则** 系统将消息发送到 `POST /api/conversations`，并将 agent 响应显示在聊天显示区域中

#### 场景：重置测试对话
- **当** 用户在测试面板中点击"清除"
- **则** 聊天历史被清除，准备新对话
