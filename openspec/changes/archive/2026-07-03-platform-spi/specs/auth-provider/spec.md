## ADDED Requirements — 新增需求

### Requirement: AuthProviderABC 定义可插拔认证接口 — AuthProviderABC defines pluggable authentication interface
系统应在 `auth/provider.py` 中定义一个 `AuthProviderABC` 抽象基类，包含以下抽象接口：`name` 属性、`description` 属性和 `authenticate(token, db)` 方法，返回 `AuthContext | None`。

#### Scenario: 具体认证提供者实现 — Concrete auth provider implementation
- **WHEN** 一个类继承 AuthProviderABC 并实现所有抽象方法
- **THEN** 它应可使用 `type="auth_provider"` 注册到 PluginRegistry

#### Scenario: 缺少抽象方法 — Missing abstract method
- **WHEN** 一个类继承 AuthProviderABC 但未实现 `authenticate()`
- **THEN** 实例化应抛出 TypeError

### Requirement: JWTAuthProvider 作为内置实现 — JWTAuthProvider as built-in implementation
系统应提供一个实现 AuthProviderABC 的 `JWTAuthProvider`。它应使用现有的 `decode_access_token()` 函数解码 JWT 访问令牌，并返回 `auth_method="jwt"` 的 `AuthContext`。

#### Scenario: 有效 JWT 令牌 — Valid JWT token
- **WHEN** 调用 `authenticate(valid_jwt_token, db)`
- **THEN** 应返回包含来自 JWT 声明的 user_id、org_id、workspace_id 和 role 的 AuthContext

#### Scenario: 无效 JWT 令牌 — Invalid JWT token
- **WHEN** 调用 `authenticate(invalid_token, db)`
- **THEN** 应返回 None（不抛出异常）

#### Scenario: 过期 JWT 令牌 — Expired JWT token
- **WHEN** 调用 `authenticate(expired_jwt_token, db)`
- **THEN** 应返回 None

### Requirement: APIKeyAuthProvider 作为内置实现 — APIKeyAuthProvider as built-in implementation
系统应提供一个实现 AuthProviderABC 的 `APIKeyAuthProvider`。它应在数据库中查找 API 密钥哈希，并返回 `auth_method="api_key"` 的 `AuthContext`。

#### Scenario: 有效的数据库支持 API 密钥 — Valid database-backed API key
- **WHEN** 调用 `authenticate(valid_api_key, db)`
- **THEN** 应返回包含密钥作用域、工作区和创建者的 AuthContext

#### Scenario: 过期的 API 密钥 — Expired API key
- **WHEN** 调用 `authenticate(expired_api_key, db)`
- **THEN** 应返回 None

#### Scenario: 未找到 API 密钥 — API key not found
- **WHEN** 调用 `authenticate(unknown_key, db)`
- **THEN** 应返回 None

### Requirement: 认证流程中的提供者迭代 — Auth provider iteration in auth flow
系统应提供一个 `resolve_auth_context(credentials, db)` 函数，按顺序遍历所有已注册的认证提供者。第一个返回非 None `AuthContext` 的提供者应被使用。如果没有提供者成功，应抛出 HTTP 401。

#### Scenario: JWT 首先成功 — JWT succeeds first
- **WHEN** 请求具有有效的 JWT 令牌
- **THEN** JWTAuthProvider 应在 APIKeyAuthProvider 被尝试前返回 AuthContext

#### Scenario: JWT 失败后 API 密钥成功 — API key succeeds after JWT fails
- **WHEN** 请求具有无效的 JWT 但有效的 API 密钥
- **THEN** JWTAuthProvider 应返回 None，然后 APIKeyAuthProvider 应返回 AuthContext

#### Scenario: 所有提供者失败 — All providers fail
- **WHEN** 请求具有无效的 JWT 和无效的 API 密钥
- **THEN** 所有提供者应返回 None，函数应抛出 HTTP 401

### Requirement: 与现有 get_auth_context 向后兼容 — Backward compatibility with existing get_auth_context
现有的 `get_auth_context()` FastAPI 依赖在迁移期间应继续工作。它应在内部委托给 `resolve_auth_context()`，保持相同的行为。

#### Scenario: 现有依赖注入正常工作 — Existing dependency injection works
- **WHEN** FastAPI 端点使用 `Depends(get_auth_context)`
- **THEN** 它应接收到与之前相同的 AuthContext（行为无变化）

### Requirement: 通过 PluginRegistry 注册认证提供者 — Auth provider registration via PluginRegistry
认证提供者应使用 `type="auth_provider"` 注册到 PluginRegistry。清单应包含提供者的 `name` 和 `description`。

#### Scenario: 注册新的认证提供者 — Register a new auth provider
- **WHEN** 使用 `type="auth_provider"` 调用 `registry.register(manifest, saml_provider)`
- **THEN** 该提供者应在认证解析期间被遍历

#### Scenario: 提供者排序 — Provider ordering
- **WHEN** 注册了多个认证提供者
- **THEN** 它们应按注册顺序遍历（先注册 = 先尝试）
