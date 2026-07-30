## ADDED Requirements — 新增需求

### Requirement: User registration — 需求：用户注册
系统应允许新用户使用邮箱和密码注册。

#### Scenario: Successful registration — 场景：注册成功
- **WHEN** 用户向 `POST /api/auth/register` 提交邮箱和密码
- **THEN** 系统创建用户记录，密码使用 bcrypt 哈希，返回 201 及用户 ID

#### Scenario: Duplicate email — 场景：重复邮箱
- **WHEN** 用户使用已存在的邮箱注册
- **THEN** 系统返回 409 Conflict

#### Scenario: Invalid input — 场景：无效输入
- **WHEN** 用户提交缺少邮箱或密码短于 8 个字符
- **THEN** 系统返回 422 及验证错误详情

### Requirement: User login — 需求：用户登录
系统应认证用户并颁发 JWT Token。

#### Scenario: Successful login — 场景：登录成功
- **WHEN** 用户向 `POST /api/auth/login` 提交正确的邮箱和密码
- **THEN** 系统返回 `access_token`（30 分钟过期）和 `refresh_token`（7 天过期）

#### Scenario: Wrong credentials — 场景：错误凭证
- **WHEN** 用户提交错误的邮箱或密码
- **THEN** 系统返回 401 Unauthorized

### Requirement: Token refresh — 需求：Token 刷新
系统应允许使用有效的 refresh_token 刷新 access_token。

#### Scenario: Successful refresh — 场景：刷新成功
- **WHEN** 用户向 `POST /api/auth/refresh` 提交有效的 refresh_token
- **THEN** 系统返回新的 access_token 和 refresh_token，旧的 refresh_token 被作废

#### Scenario: Expired refresh token — 场景：refresh_token 过期
- **WHEN** 用户提交过期或无效的 refresh_token
- **THEN** 系统返回 401 Unauthorized

### Requirement: Get current user — 需求：获取当前用户
系统应返回已认证用户的个人信息。

#### Scenario: Authenticated user info — 场景：已认证用户信息
- **WHEN** 用户使用有效 JWT 发送 `GET /api/auth/me`
- **THEN** 系统返回用户 ID、邮箱和 created_at

### Requirement: Dual authentication support — 需求：双认证支持
系统应在所有端点上同时支持 JWT Bearer Token 和 API Key 认证。

#### Scenario: JWT authentication — 场景：JWT 认证
- **WHEN** 请求包含 `Authorization: Bearer <jwt_token>`
- **THEN** 系统认证用户并设置请求上下文

#### Scenario: API Key authentication — 场景：API Key 认证
- **WHEN** 请求包含 `Authorization: Bearer <api_key>`
- **THEN** 系统通过 API Key 查找进行认证（向后兼容）

### Requirement: User data model — 需求：用户数据模型
系统应在 `users` 表中存储用户，包含 id（UUID）、email（唯一）、hashed_password、created_at、updated_at。

#### Scenario: Database schema — 场景：数据库模式
- **WHEN** Alembic 迁移运行
- **THEN** 创建 `users` 表，email 上建立唯一索引
