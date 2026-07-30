## ADDED Requirements — 新增需求

### 需求：JWT 声明增强
系统应在访问令牌中包含 `org_id`、`workspace_id` 和 `role` 声明，以及现有的 `sub`、`type`、`exp` 和 `iat` 声明。

#### 场景：登录返回增强令牌
- **当** 用户通过 POST `/auth/login` 使用有效凭据进行认证
- **则** 系统返回带有声明的访问令牌：`{sub: user_id, type: "access", org_id: "...", workspace_id: "...", role: "editor", exp: ..., iat: ...}`。`workspace_id` 和 `role` 对应于用户最近使用的工作区，或者他们有访问权限的第一个工作区。

#### 场景：没有工作区成员资格的登录
- **当** 用户进行认证但没有工作区成员资格
- **则** 系统返回 `org_id: null`、`workspace_id: null`、`role: null` 的访问令牌

### 需求：认证上下文依赖
系统应提供 `get_auth_context()` FastAPI 依赖项，将认证请求解析为 `AuthContext` 数据类，包含：`user_id`、`org_id`、`workspace_id`、`role`、`auth_method`（"jwt" 或 "api_key"）、`api_key_scope`（可空）。

#### 场景：JWT 认证上下文
- **当** 请求通过 JWT Bearer 令牌进行认证
- **则** `get_auth_context()` 返回 `AuthContext(user_id=..., org_id=..., workspace_id=..., role=..., auth_method="jwt", api_key_scope=None)`

#### 场景：工作区 API 密钥认证上下文
- **当** 请求通过工作区级 API 密钥进行认证
- **则** `get_auth_context()` 返回 `AuthContext(user_id=created_by, org_id=..., workspace_id=..., role="admin", auth_method="api_key", api_key_scope="workspace")`

#### 场景：系统 API 密钥认证上下文
- **当** 请求通过系统级 API 密钥进行认证
- **则** `get_auth_context()` 返回 `AuthContext(user_id=created_by, org_id=None, workspace_id=None, role=None, auth_method="api_key", api_key_scope="system")`

### 需求：工作区切换
系统应提供切换活动工作区上下文的端点。这将签发带有目标工作区声明的新访问和刷新令牌。

#### 场景：切换工作区
- **当** 认证用户发送 POST `/auth/switch-workspace`，包含 `{workspace_id: "..."}`
- **则** 系统验证用户是目标工作区的成员，签发带有更新后的 `org_id`、`workspace_id` 和 `role` 声明的新令牌，并返回 `{access_token, refresh_token, token_type}`

#### 场景：切换到不可访问的工作区
- **当** 认证用户使用他们不是成员的工作区发送 POST `/auth/switch-workspace`
- **则** 系统返回 `403 Forbidden`

### 需求：登录响应包含可访问的工作区
登录响应应包含用户有访问权限的工作区列表，允许客户端展示工作区选择器。

#### 场景：登录返回工作区列表
- **当** 用户成功登录
- **则** 响应包含 `{access_token, refresh_token, token_type, workspaces: [{id, name, slug, org_id, role}, ...]}`

### 需求：SSO 扩展点
`UserModel` 应包含一个可选的 `sso_id` 字段（可空字符串），用于存储外部身份提供者的用户标识符。此字段不用于本地认证流程，但为未来 SSO 集成（OIDC/SAML）预留。

#### 场景：使用 sso_id 创建的用户
- **当** 通过 SSO 同步（未来）创建用户
- **则** `sso_id` 字段存储外部身份提供者的用户 ID

#### 场景：本地用户 sso_id 为 null
- **当** 用户通过邮箱/密码注册
- **则** `sso_id` 字段为 null

### 需求：令牌刷新保留工作区上下文
刷新令牌时，系统应保留刷新令牌中的工作区上下文，或从用户当前成员资格重新派生。

#### 场景：刷新保留工作区
- **当** 用户在访问令牌中拥有工作区上下文时刷新令牌
- **则** 新令牌保持相同的 `org_id`、`workspace_id` 和 `role` 声明
