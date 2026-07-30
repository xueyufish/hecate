## ADDED Requirements — 新增需求

### Requirement: SCIM 2.0 用户端点 — SCIM 2.0 User endpoints
系统应在 `/scim/v2/Users` 暴露符合 SCIM 2.0 的用户管理端点，支持 POST、GET（列表）、GET（单个）、PUT、PATCH 和 DELETE 操作，符合 RFC 7644。

#### Scenario: 通过 SCIM POST 创建用户 — Create user via SCIM POST
- **WHEN** 收到对 `/scim/v2/Users` 的 POST 请求，带有有效的 SCIM User JSON 主体和有效的 bearer 令牌
- **THEN** 系统应创建 UserModel，其中 `email=userName`、`display_name=displayName`、`given_name=name.givenName`、`family_name=name.familyName`、`external_id=externalId`、`active=True`、`sso_id=userName`
- **AND** 应返回 HTTP 201 以及 SCIM User 表示，包括 `id`、`meta.location` 和 `meta.resourceType=User`

#### Scenario: 带分页的用户列表 — List users with pagination
- **WHEN** 收到对 `/scim/v2/Users?startIndex=1&count=10` 的 GET 请求
- **THEN** 系统应返回 ListResponse，包含 `totalResults`、`startIndex`、`itemsPerPage` 和 SCIM User 对象的 `resources` 数组

#### Scenario: 按 userName 过滤用户 — Filter users by userName
- **WHEN** 收到对 `/scim/v2/Users?filter=userName eq 'john@example.com'` 的 GET 请求
- **THEN** 系统应解析 SCIM 过滤器，通过 email 查询 UserModel，并在 ListResponse 中返回匹配的用户

#### Scenario: 按 ID 获取用户 — Get user by ID
- **WHEN** 收到对现有用户的 `/scim/v2/Users/{id}` 的 GET 请求
- **THEN** 系统应返回带所有属性的 SCIM User 表示

#### Scenario: 获取不存在的用户 — Get non-existent user
- **WHEN** 收到对不存在用户的 `/scim/v2/Users/{id}` 的 GET 请求
- **THEN** 系统应返回 SCIM 错误响应，`status=404` 和 `detail="Resource {id} not found"`

#### Scenario: 通过 PUT 更新用户（全量替换） — Update user via PUT (full replacement)
- **WHEN** 收到对 `/scim/v2/Users/{id}` 的 PUT 请求，带有完整的 SCIM User 主体
- **THEN** 系统应替换 UserModel 上的所有可编辑字段，并返回 HTTP 200 和更新后的 SCIM User

#### Scenario: 通过 PATCH 部分更新 — Partial update via PATCH
- **WHEN** 收到对 `/scim/v2/Users/{id}` 的 PATCH 请求，带有包含 `Operations` 的 PatchOp 主体
- **THEN** 系统应将每个操作（replace、add、remove）应用到 UserModel 字段，并返回 HTTP 200 和更新后的用户

#### Scenario: 通过 PATCH active=false 取消配置 — Deprovision user via PATCH active=false
- **WHEN** PATCH 请求将 `active` 设置为 `false`
- **THEN** 系统应设置 `UserModel.active=False`，该用户应不能再进行认证

#### Scenario: 删除用户 — Delete user
- **WHEN** 收到对 `/scim/v2/Users/{id}` 的 DELETE 请求
- **THEN** 系统应设置 `UserModel.active=False`（软删除）并返回 HTTP 204 No Content

#### Scenario: 重复 userName 被拒绝 — Duplicate userName rejected
- **WHEN** POST 请求创建的用户名 `userName` 已存在
- **THEN** 系统应返回 SCIM 错误，`status=409`、`scimType=uniqueness`

### Requirement: SCIM 2.0 组端点 — SCIM 2.0 Group endpoints
系统应在 `/scim/v2/Groups` 暴露符合 SCIM 2.0 的组管理端点，支持 POST、GET、PATCH 和 DELETE 操作，用于工作区/团队成员关系同步。

#### Scenario: 通过 SCIM POST 创建组 — Create group via SCIM POST
- **WHEN** 收到对 `/scim/v2/Groups` 的 POST 请求，带有 `displayName` 和 `members` 数组
- **THEN** 系统应创建映射到工作区角色/团队的组记录，并返回 HTTP 201

#### Scenario: 列出组 — List groups
- **WHEN** 收到对 `/scim/v2/Groups` 的 GET 请求
- **THEN** 系统应返回所有组的 ListResponse，包含成员引用

#### Scenario: 通过 PATCH 更新组成员 — Update group membership via PATCH
- **WHEN** 对 `/scim/v2/Groups/{id}` 的 PATCH 请求添加或移除成员
- **THEN** 系统应相应更新组成员关系

#### Scenario: 删除组 — Delete group
- **WHEN** 收到对 `/scim/v2/Groups/{id}` 的 DELETE 请求
- **THEN** 系统应软删除该组并返回 HTTP 204

### Requirement: SCIM 发现端点 — SCIM discovery endpoints
系统应暴露 SCIM 2.0 发现端点，用于 ServiceProviderConfig、Schemas 和 ResourceTypes，符合 RFC 7643 第 4 节。

#### Scenario: 获取 ServiceProviderConfig — Get ServiceProviderConfig
- **WHEN** 收到对 `/scim/v2/ServiceProviderConfig` 的 GET 请求
- **THEN** 系统应返回能力，包括 `patch.supported=true`、`filter.supported=true`、`sort.supported=true`、`etag.supported=true` 和 `bulk.supported=false`

#### Scenario: 获取 Schemas — Get Schemas
- **WHEN** 收到对 `/scim/v2/Schemas` 的 GET 请求
- **THEN** 系统应返回核心 User、Group 和企业扩展模式的模式定义

#### Scenario: 获取 ResourceTypes — Get ResourceTypes
- **WHEN** 收到对 `/scim/v2/ResourceTypes` 的 GET 请求
- **THEN** 系统应返回 User 和 Group 资源类型的定义，包括其模式、端点和描述

### Requirement: SCIM 认证 — SCIM authentication
系统应使用单独的 SCIM bearer 令牌认证 SCIM API 请求，区别于 JWT 和 API 密钥认证。

#### Scenario: 有效的 SCIM bearer 令牌 — Valid SCIM bearer token
- **WHEN** SCIM 请求包含 `Authorization: Bearer {scim_token}`，且令牌匹配配置的 `SCIM_BEARER_TOKEN` 设置
- **THEN** 请求应被处理

#### Scenario: 缺少或无效的 SCIM 令牌 — Missing or invalid SCIM token
- **WHEN** SCIM 请求没有 Authorization 头或令牌无效
- **THEN** 系统应返回 HTTP 401 及 SCIM 错误响应

### Requirement: SCIM 错误响应格式 — SCIM error response format
系统应按 RFC 7644 第 3.12 节的 SCIM 错误格式返回错误，包含 `schemas`、`status`、`scimType`（适用时）和 `detail` 字段。

#### Scenario: 验证错误 — Validation error
- **WHEN** SCIM 请求主体验证失败
- **THEN** 系统应返回 `{"schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"], "status": "400", "scimType": "invalidSyntax", "detail": "..."}`

#### Scenario: 未找到错误 — Not found error
- **WHEN** SCIM 资源未找到
- **THEN** 系统应返回 `{"schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"], "status": "404", "detail": "Resource not found"}`

### Requirement: SCIM 用户模型扩展 — SCIM user model extension
系统应添加 `external_id`（String 255，可空）、`display_name`（String 255，可空）、`given_name`（String 128，可空）、`family_name`（String 128，可空）和 `active`（Boolean，默认为 True）字段到 UserModel，以支持 SCIM 配置。

#### Scenario: 新用户字段默认值 — New user fields default values
- **WHEN** 迁移后加载现有用户
- **THEN** `active` 应为 `True`，`external_id`、`display_name`、`given_name`、`family_name` 应为 `None`

#### Scenario: 非活跃用户不能认证 — Inactive user cannot authenticate
- **WHEN** `active=False` 的用户尝试通过任何认证提供者认证
- **THEN** 系统应拒绝认证，返回 None 或 HTTP 401
