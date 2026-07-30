## ADDED Requirements — 新增需求

### 需求：API 密钥数据库模型
系统应将 API 密钥存储在 `ApiKeyModel` 数据库表中，包含以下字段：`id`（UUID PK）、`name`（字符串，用户友好标签）、`key_hash`（原始密钥的 SHA-256 哈希）、`key_prefix`（显示用的前 8 个字符）、`scope`（枚举：`system` 或 `workspace`）、`org_id`（可空 FK 指向 OrganizationModel）、`workspace_id`（可空 FK 指向 WorkspaceModel）、`created_by`（FK 指向 UserModel）、`last_used_at`（可空日期时间）、`expires_at`（可空日期时间）、`is_active`（布尔值），外加继承的 BaseModel 字段（时间戳、软删除）。

#### 场景：API 密钥模型字段
- **当** 使用 `scope: "workspace"` 创建 API 密钥
- **则** `org_id` 和 `workspace_id` 为必填，设置为指定的组织和工区域

#### 场景：系统级密钥无工作区绑定
- **当** 使用 `scope: "system"` 创建 API 密钥
- **则** `org_id` 和 `workspace_id` 为 null

### 需求：API 密钥生成
系统应生成格式为 `hcat_<base62_32chars>` 的 API 密钥。原始密钥应在创建时仅向用户展示一次，绝不以明文存储。仅持久化 SHA-256 哈希。

#### 场景：创建工作区 API 密钥
- **当** 工作区管理员发送 POST `/api/api-keys`，包含 `{name: "Production Key", scope: "workspace", workspace_id: "..."}`
- **则** 系统生成密钥，存储其 SHA-256 哈希和前缀，返回 `201` 及 `{id, name, key: "hcat_...", scope, workspace_id, created_at}`。原始密钥不存储。

#### 场景：创建系统 API 密钥
- **当** 用户发送 POST `/api/api-keys`，包含 `{name: "Admin Key", scope: "system"}`
- **则** 系统生成系统级密钥，返回 `201` 及原始密钥

#### 场景：密钥仅展示一次
- **当** 用户创建 API 密钥后调用 GET `/api/api-keys/{id}`
- **则** 响应包含 `{id, name, key_prefix: "hcat_abcd...", scope, ...}` 但不包含完整的原始密钥

### 需求：API 密钥验证
系统应通过计算传入 Bearer 令牌的 SHA-256 并与存储的 `key_hash` 进行比较来验证 API 密钥。验证成功后，系统应更新 `last_used_at` 并返回完整的密钥上下文（scope、org_id、workspace_id）。

#### 场景：有效的工作区密钥
- **当** 请求到达时带有 `Authorization: Bearer hcat_<valid_workspace_key>`
- **则** 系统将密钥解析为其工作区上下文，并将 `{org_id, workspace_id, scope: "workspace"}` 注入认证上下文

#### 场景：有效的系统密钥
- **当** 请求到达时带有有效的系统级密钥
- **则** 系统将 `{scope: "system", org_id: null, workspace_id: null}` 注入认证上下文

#### 场景：无效或已撤销的密钥
- **当** 请求到达时密钥的哈希与任何活动记录不匹配
- **则** 系统返回 `401 Unauthorized`

#### 场景：已过期的密钥
- **当** 请求到达时密钥的 `expires_at` 已过
- **则** 系统返回 `401 Unauthorized` 并提示 "API key expired"

### 需求：API 密钥轮换
系统应支持通过创建替换密钥并立即撤销旧密钥来轮换 API 密钥。

#### 场景：轮换 API 密钥
- **当** 用户发送 POST `/api/api-keys/{id}/rotate`
- **则** 系统创建具有相同作用域和绑定的新密钥，设置旧密钥 `is_active = false`，返回 `200` 及新的原始密钥。旧密钥立即失效。

### 需求：API 密钥撤销
系统应支持明确的 API 密钥撤销（软删除）。已撤销的密钥不再有效用于认证。

#### 场景：撤销 API 密钥
- **当** 用户发送 DELETE `/api/api-keys/{id}`
- **则** 系统设置密钥的 `is_active = false`，返回 `204`

#### 场景：列出 API 密钥
- **当** 用户发送 GET `/api/api-keys`
- **则** 系统返回用户创建的分页 API 密钥列表，显示 `{id, name, key_prefix, scope, is_active, last_used_at, expires_at, created_at}`

### 需求：环境变量 API 密钥弃用
系统应在弃用期内继续支持 `HECATE_API_KEYS` 环境变量，每次使用时记录警告。环境变量密钥被视为系统级密钥。

#### 场景：环境变量密钥仍可用但带警告
- **当** 请求使用来自 `HECATE_API_KEYS` 环境变量的密钥进行认证
- **则** 系统接受该密钥，记录弃用警告，并将其视为系统级密钥

#### 场景：数据库密钥优先
- **当** 密钥同时存在于环境变量和数据库中
- **则** 系统优先解析数据库记录，忽略环境变量匹配
