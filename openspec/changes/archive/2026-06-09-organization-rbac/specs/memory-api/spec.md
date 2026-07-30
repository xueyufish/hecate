## MODIFIED Requirements — 修改的需求

### 需求：认证与授权

- 所有端点应通过 `get_auth_context()` 依赖项要求认证，替换之前的 `verify_api_key` 依赖
- 代理内存块应对代理工作区内具有 `editor` 或 `admin` 角色的用户可访问
- 用户内存应仅对用户本人可访问
- 内存操作的 workspace_id 应从认证的工作区上下文（JWT 声明或 API 密钥作用域）解析，而非从请求参数获取
- 系统级 API 密钥应绕过工作区所有权检查

#### 场景：工作区限定的内存访问
- **当** 工作区 W1 中具有 `editor` 角色的用户发送 GET `/api/agents/{agent_id}/memory/blocks`，且代理属于工作区 W1
- **则** 系统返回内存块

#### 场景：跨工作区内存访问被拒绝
- **当** 工作区 W1 中具有 `editor` 角色的用户尝试访问工作区 W2 中代理的内存块
- **则** 系统返回 `403 Forbidden`

#### 场景：来自认证的工作区上下文
- **当** 使用来自 JWT 或 API 密钥的工作区上下文调用内存端点
- **则** 系统对所有查询使用认证的 workspace_id，忽略请求体中的任何 workspace_id

### 需求：工作区隔离

- 所有现有内存端点应通过从认证上下文（而非代理查找或请求参数）解析的 `workspace_id` 强制实施工作区隔离
- `workspace_id` 应由 `get_auth_context()` 依赖项自动解析
- 服务层方法应从认证上下文接收 `workspace_id`，而非直接参数
- 查询应包含与认证工作区匹配的 `workspace_id` 过滤条件

#### 场景：内存查询使用认证工作区
- **当** 用户访问内存端点时认证上下文中带有工作区 W1
- **则** 无论请求体中是否有任何 workspace_id，所有查询均按 `workspace_id = W1.id` 过滤
