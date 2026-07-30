## ADDED Requirements — 新增需求

### 需求：组织 CRUD
系统应提供创建、读取、更新和软删除组织的 REST API 端点。每个组织应有唯一的 `slug`，用于人类可读的寻址。`slug` 应在未提供时从组织名称自动生成，并在创建后不可更改。

#### 场景：创建组织
- **当** 认证用户发送 POST `/api/orgs`，包含 `{name: "Acme Corp", slug: "acme"}`
- **则** 系统创建 UUID `id` 的 OrganizationModel，设置 `owner_id` 为请求用户，返回 `201` 及 `{id, name, slug, owner_id, created_at}`

#### 场景：使用自动 slug 创建组织
- **当** 认证用户发送 POST `/api/orgs`，包含 `{name: "Acme Corp"}`（无 slug）
- **则** 系统从名称自动生成 `slug: "acme-corp"` 并返回 `201`

#### 场景：重复 slug 被拒绝
- **当** 认证用户使用已存在的 `slug` 发送 POST `/api/orgs`
- **则** 系统返回 `409 Conflict` 及错误详情

#### 场景：列出组织
- **当** 认证用户发送 GET `/api/orgs`
- **则** 系统返回用户为所有者的分页组织列表

#### 场景：按 ID 获取组织
- **当** 认证用户发送 GET `/api/orgs/{org_id}`
- **则** 如果用户是所有者，系统返回组织详情

#### 场景：更新组织
- **当** 组织所有者发送 PATCH `/api/orgs/{org_id}`，包含 `{name: "Acme Inc"}`
- **则** 系统更新组织名称并返回 `200` 及更新后的数据

#### 场景：删除组织
- **当** 组织所有者发送 DELETE `/api/orgs/{org_id}`
- **则** 系统软删除组织及所有关联的工作区，前提是工作区中没有活动资源

### 需求：组织所有权
每个组织应有且仅有一个 `owner_id`（指向 UserModel 的外键）。所有者是该组织下所有工作区的初始管理员。所有权可以转让给组织中另一个成员用户。

#### 场景：创建时设置所有者
- **当** 用户创建组织
- **则** `owner_id` 设置为创建用户的 ID

#### 场景：转让所有权
- **当** 当前所有者发送 POST `/api/orgs/{org_id}/transfer-ownership`，包含 `{new_owner_id: "..."}`
- **则** 系统验证新所有者是组织成员，更新 `owner_id`，并确保新所有者拥有所有组织工作区的 admin 角色

### 需求：工作区 CRUD
系统应在组织内提供创建、读取、更新和软删除工作区的 REST API 端点。每个工作区应有唯一 `slug`，限定于其父组织（非全局唯一）。工作区是资源隔离边界——所有租户范围资源属于且仅属于一个工作区。

#### 场景：在组织中创建工作区
- **当** 组织所有者发送 POST `/api/orgs/{org_id}/workspaces`，包含 `{name: "Production", slug: "prod"}`
- **则** 系统创建带 `org_id` FK 的 WorkspaceModel，设置 `slug: "prod"`，在 WorkspaceMemberModel 中将创建者添加为工作区管理员，并返回 `201`

#### 场景：工作区 slug 限定于组织
- **当** 组织所有者在组织 A 中创建 `slug: "default"` 的工作区，而另一个组织也有一个 `slug: "default"` 的工作区
- **则** 两个操作都成功——slug 唯一性是按组织而非全局的

#### 场景：列出组织中的工作区
- **当** 组织成员发送 GET `/api/orgs/{org_id}/workspaces`
- **则** 系统返回该组织中用户为成员的分页工作区列表

#### 场景：更新工作区
- **当** 工作区管理员发送 PATCH `/api/orgs/{org_id}/workspaces/{workspace_id}`，包含 `{name: "Staging"}`
- **则** 系统更新工作区名称并返回 `200`

#### 场景：删除包含资源的工作区
- **当** 工作区管理员发送 DELETE `/api/orgs/{org_id}/workspaces/{workspace_id}` 且工作区包含活动（未删除）资源
- **则** 系统返回 `409 Conflict` 及错误详情，列出必须首先删除的资源类型

#### 场景：删除空工作区
- **当** 工作区管理员发送 DELETE 且工作区没有活动资源
- **则** 系统软删除工作区及其成员记录

### 需求：默认组织和默认工作区引导
系统应在初始数据库迁移期间创建默认组织（`id: 00000000-0000-0000-0000-000000000000`、`slug: "default"`）和默认工作区（`id: 00000000-0000-0000-0000-000000000000`、`slug: "default"`、`org_id: 00000000-...`）。这确保了与现有单租户部署的向后兼容。

#### 场景：全新安装引导
- **当** 数据库迁移在全新数据库上运行
- **则** 系统创建含零 UUID ID 的默认组织和默认工作区

#### 场景：从现有部署升级
- **当** 数据库迁移在现有数据库上运行，资源具有零 UUID 的 workspace_id
- **则** 现有资源自动属于默认工作区，因为它们的 workspace_id 与默认工作区 ID 匹配

### 需求：工作区外键强制
所有当前具有 `workspace_id` 列的模型（AgentModel、WorkflowModel、SkillModel、ToolModel、KnowledgeBaseModel、PromptModel、MemoryBlockModel、MemoryModel、KnowledgeMemoryModel）应具有指向 WorkspaceModel.id 的外键约束。该 FK 应为非空，默认值为零 UUID 的默认工作区。

#### 场景：使用有效 workspace_id 创建资源
- **当** 用户使用指向现有工作区的 `workspace_id` 创建代理
- **则** 操作成功，代理限定于该工作区

#### 场景：使用无效 workspace_id 创建资源
- **当** 用户使用的 `workspace_id` 不指向任何工作区
- **则** 数据库抛出 FK 违规，API 返回 `400 Bad Request`

### 需求：工作区限定的资源列表
所有列出租户范围资源（agents、workflows、skills、tools、knowledge bases、prompts、memory blocks、memories、knowledge memories）的 API 端点应按认证的工作区上下文过滤结果。

#### 场景：列出工作区中的代理
- **当** 具有工作区上下文的用户发送 GET `/api/agents`
- **则** 系统仅返回 `workspace_id` 与认证工作区匹配的代理

#### 场景：跨工作区隔离
- **当** 用户 A 是工作区 W1 的成员但不是 W2 的成员
- **则** 使用工作区 W1 上下文列出代理仅返回 W1 的代理；尝试访问 W2 资源返回 `403 Forbidden`
