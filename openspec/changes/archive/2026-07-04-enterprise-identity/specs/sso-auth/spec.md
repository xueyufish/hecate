## ADDED Requirements — 新增需求

### Requirement: OIDCAuthProvider 实现 AuthProviderABC — OIDCAuthProvider implements AuthProviderABC
系统应在 `auth/oidc_provider.py` 中定义 `OIDCAuthProvider(AuthProviderABC)`，通过 OpenID Connect 授权码流程使用 Authlib 的 Starlette 客户端认证用户。

#### Scenario: OIDC 提供者初始化 — OIDC provider initialization
- **WHEN** 使用 `client_id`、`client_secret`、`discovery_url` 和 `scope` 配置创建 OIDCAuthProvider
- **THEN** 提供者应注册一个 Authlib `OidcClient`，在首次使用时从发现 URL 获取 IdP 元数据

#### Scenario: 发起 OIDC 登录 — Initiate OIDC login
- **WHEN** 用户导航到 `/auth/sso/oidc/login`
- **THEN** 系统应重定向到 IdP 授权端点，带 PKCE 质询和 state 参数
- **AND** 重定向 URL 应包含 `client_id`、`redirect_uri`、`scope=openid profile email` 和 `response_type=code`

#### Scenario: 使用有效授权码的 OIDC 回调 — OIDC callback with valid authorization code
- **WHEN** IdP 回调到 `/auth/sso/oidc/callback`，带有有效的 `code` 和匹配的 `state`
- **THEN** 系统应将 code 交换为访问令牌和 ID 令牌
- **AND** 应从 IdP 的 userinfo 端点获取用户信息
- **AND** 应将 `sub` 声明映射到 `UserModel.sso_id` 用于用户解析

#### Scenario: 首次 OIDC 登录时的 JIT 配置 — JIT provisioning on first OIDC login
- **WHEN** OIDC userinfo 的 `sub` 声明不匹配任何现有的 `UserModel.sso_id`
- **THEN** 系统应创建新的 UserModel，`sso_id=sub`、`email=userinfo.email`、`display_name=userinfo.name`、`auth_method="sso"`、`active=True` 和随机 `hashed_password`
- **AND** 应为新创建的用户签发 JWT 令牌

#### Scenario: state 无效的 OIDC 回调 — OIDC callback with invalid state
- **WHEN** 回调请求的 `state` 参数与会话中存储的值不匹配
- **THEN** 系统应返回 HTTP 400，错误为 "Invalid state parameter"

#### Scenario: code 过期或无效的 OIDC 回调 — OIDC callback with expired or invalid code
- **WHEN** IdP 返回错误或 code 交换失败
- **THEN** 系统应返回 HTTP 401，错误为 "OIDC authentication failed"

### Requirement: SAMLAuthProvider 实现 AuthProviderABC — SAMLAuthProvider implements AuthProviderABC
系统应在 `auth/saml_provider.py` 中定义 `SAMLAuthProvider(AuthProviderABC)`，使用 python3-saml 通过 SAML 2.0 SP 发起的 SSO 认证用户。

#### Scenario: SAML 提供者初始化 — SAML provider initialization
- **WHEN** 使用 `sp_entity_id`、`sp_acs_url`、`idp_entity_id`、`idp_sso_url` 和 `idp_x509_cert` 配置创建 SAMLAuthProvider
- **THEN** 提供者应使用 SP 和 IdP 元数据初始化一个 OneLogin_Saml2_Auth 实例

#### Scenario: 发起 SAML 登录 — Initiate SAML login
- **WHEN** 用户导航到 `/auth/sso/saml/login`
- **THEN** 系统应生成 SAML AuthnRequest 并重定向到 IdP SSO URL

#### Scenario: 使用有效断言的 SAML ACS — SAML ACS with valid assertion
- **WHEN** IdP 将 SAML 响应 POST 到 `/auth/sso/saml/acs`，带有有效的签名断言
- **THEN** 系统应验证 XML 签名，检查断言条件（NotBefore、NotOnOrAfter）
- **AND** 应提取 `NameID` 作为用户标识符，并映射到 `UserModel.sso_id`

#### Scenario: 首次 SAML 登录时的 JIT 配置 — JIT provisioning on first SAML login
- **WHEN** SAML NameID 不匹配任何现有的 `UserModel.sso_id`
- **THEN** 系统应创建新的 UserModel，`sso_id=NameID`、来自 SAML 属性语句的 email、`active=True`

#### Scenario: 签名无效的 SAML ACS — SAML ACS with invalid signature
- **WHEN** SAML 响应具有无效或缺失的签名
- **THEN** 系统应返回 HTTP 401，错误为 "SAML signature validation failed"

### Requirement: LDAPAuthProvider 实现 AuthProviderABC — LDAPAuthProvider implements AuthProviderABC
系统应在 `auth/ldap_provider.py` 中定义 `LDAPAuthProvider(AuthProviderABC)`，使用 ldap3 通过 asyncio 传输的 LDAP 绑定认证用户。

#### Scenario: LDAP 提供者初始化 — LDAP provider initialization
- **WHEN** 使用 `server_url`、`base_dn`、`bind_dn`、`bind_password`、`search_filter` 和 `use_ssl` 配置创建 LDAPAuthProvider
- **THEN** 提供者应使用配置的服务器初始化 ldap3 连接池

#### Scenario: 使用有效凭据的 LDAP 认证 — LDAP authentication with valid credentials
- **WHEN** 调用 `authenticate(token, db)`，其中 token 是 base64 编码的 `username:password` 字符串
- **THEN** 系统应使用用户的 DN（通过搜索过滤器解析）绑定到 LDAP 服务器
- **AND** 如果绑定成功，应通过 `sso_id=username` 查询 UserModel 或通过 JIT 创建
- **AND** 应返回 `auth_method="ldap"` 的 AuthContext

#### Scenario: 凭据无效的 LDAP 认证 — LDAP authentication with invalid credentials
- **WHEN** LDAP 绑定失败，返回 `INVALID_CREDENTIALS`
- **THEN** 系统应返回 `None`（认证提供者链继续到下一个提供者）

#### Scenario: LDAP 服务器不可达 — LDAP server unreachable
- **WHEN** LDAP 服务器不可达或超时
- **THEN** 系统应记录错误并返回 `None`（不阻塞认证链）

### Requirement: SSO 认证上下文方法 — SSO auth context method
系统应扩展 AuthContext，支持 `"sso"` 和 `"ldap"` 作为有效的 `auth_method` 值，除了现有的 `"jwt"` 和 `"api_key"`。

#### Scenario: 来自 SSO 认证的 AuthContext — AuthContext from SSO authentication
- **WHEN** 用户通过 OIDC 或 SAML 成功认证
- **THEN** AuthContext 应具有 `auth_method="sso"`、从用户的工作区成员关系解析的 `user_id`、`org_id`、`workspace_id` 和 `role`

#### Scenario: 来自 LDAP 认证的 AuthContext — AuthContext from LDAP authentication
- **WHEN** 用户通过 LDAP 成功认证
- **THEN** AuthContext 应具有 `auth_method="ldap"`，字段与 SSO 相同

### Requirement: Settings 中的 SSO 配置 — SSO configuration in Settings
系统应向 Settings 类添加 SSO 提供者配置，包含 OIDC、SAML 和 LDAP 提供者的字段。

#### Scenario: OIDC 配置 — OIDC configuration
- **WHEN** Settings 包含 `SSO_OIDC_CLIENT_ID`、`SSO_OIDC_CLIENT_SECRET`、`SSO_OIDC_DISCOVERY_URL`
- **THEN** OIDCAuthProvider 应在认证解析器中注册

#### Scenario: SAML 配置 — SAML configuration
- **WHEN** Settings 包含 `SSO_SAML_SP_ENTITY_ID`、`SSO_SAML_IDP_ENTITY_ID`、`SSO_SAML_IDP_SSO_URL`、`SSO_SAML_IDP_X509_CERT`
- **THEN** SAMLAuthProvider 应在认证解析器中注册

#### Scenario: LDAP 配置 — LDAP configuration
- **WHEN** Settings 包含 `SSO_LDAP_SERVER_URL`、`SSO_LDAP_BASE_DN`、`SSO_LDAP_BIND_DN`、`SSO_LDAP_BIND_PASSWORD`
- **THEN** LDAPAuthProvider 应在认证解析器中注册

#### Scenario: 未配置 SSO — No SSO configured
- **WHEN** 未提供 SSO 设置
- **THEN** 不应注册 SSO 提供者，现有的 JWT/APIKey 认证继续工作
