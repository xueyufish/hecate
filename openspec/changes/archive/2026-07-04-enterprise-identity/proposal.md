## Why — 为什么

企业客户需要在受监管环境中部署 Hecate 时使用联合身份、自动化用户生命周期管理、成本治理和集中式密钥管理。Platform SPI（AuthProviderABC、ChannelABC）和 Cost/Quota 基础设施已到位，但缺乏 Fortune 500 组织所要求的企业级身份集成：通过 OIDC/SAML/LDAP 的 SSO、来自 Azure AD/Okta 的 SCIM 2.0 目录同步、带预测的按组织预算执行，以及用于动态凭证管理的 HashiCorp Vault / AWS Secrets Manager。

## What Changes — 变更内容

- **SSO 认证提供者**：实现 OIDCAuthProvider、SAMLAuthProvider 和 LDAPAuthProvider 作为 AuthProviderABC 子类。添加 OAuth/OIDC 授权码流程，带 JIT（即时）用户配置。SAML SP 发起的 SSO，带断言解析。LDAP 绑定认证，带异步 ldap3。
- **SCIM 2.0 目录同步**：暴露符合 RFC 7643/7644 的 `/scim/v2/Users` 和 `/scim/v2/Groups` 端点。支持 Azure AD 和 Okta 配置模式：用户 CRUD、组成员同步、分页、SCIM 过滤器语法、通过 `active=false` 进行软删除。
- **预算管理**：使用 `org` 和 `agent` 作用域级别扩展 QuotaModel。添加 BudgetModel 用于周期性预算，带预测投影、计费报告和成本异常检测。与 CostService 集成用于实时支出跟踪，与 AlertService 集成用于阈值通知。
- **企业 Vault 集成**：定义 SecretProviderABC 抽象接口。实现 HashiCorpVaultProvider、AWSSecretsManagerProvider 和 AzureKeyVaultProvider。支持 OAuth 2.0 令牌交换（RFC 8693）用于每个 Agent 身份到 vault 的认证。动态短期凭证替换 Settings 中的静态 API 密钥。

## Capabilities — 能力

### 新能力

- `sso-auth`：通过 OIDC、SAML 和 LDAP 协议的 SSO 认证。实现 AuthProviderABC，带授权码流程、JIT 用户配置、断言解析和 LDAP 绑定认证。将外部身份提供者声明映射到本地 UserModel.sso_id。
- `scim-provisioning`：用于自动化用户生命周期管理的 SCIM 2.0 目录同步端点。符合 RFC 7643/7644。支持 Azure AD 和 Okta 配置模式，包括用户 CRUD、组同步、分页、过滤器语法和取消配置。
- `budget-management`：按组织、工作区和 Agent 的支出限制，带硬/软上限执行。成本预测、计费报告和异常检测。扩展现有的 QuotaService 和 CostService 基础设施。
- `vault-integration`：SecretProviderABC，带 HashiCorp Vault、AWS Secrets Manager 和 Azure Key Vault 后端。通过 OAuth 2.0 令牌交换的每个 Agent 身份认证。动态短期凭证配置。

### 修改的能力

（无——所有新能力建立在现有的 AuthProviderABC、QuotaService、CostService 和 AlertService 之上，不改变其规范级行为）

## Impact — 影响

- **新模块**：`src/hecate/auth/oidc_provider.py`、`src/hecate/auth/saml_provider.py`、`src/hecate/auth/ldap_provider.py`、`src/hecate/scim/`、`src/hecate/budget/`、`src/hecate/vault/`
- **修改的现有文件**：`src/hecate/models/user.py`（为 SCIM 添加 `external_id`、`active`、`display_name`、`given_name`、`family_name` 字段）、`src/hecate/models/quota.py`（向 QuotaScope 枚举添加 `ORG` 和 `AGENT`）、`src/hecate/main.py`（注册 SCIM 路由、vault 初始化）
- **新依赖**：`authlib`（OIDC/OAuth）、`python3-saml` 或 `xmlsec`（SAML）、`ldap3`（LDAP）、`scim2-models`（SCIM 模式）、`hvac`（HashiCorp Vault）、`aiobotocore`（AWS Secrets Manager）
- **数据库迁移**：User 模型字段、预算表、vault 配置表、SCIM 组/成员关系表
- **配置**：SSO 提供者的新设置组（client_id、client_secret、discovery_url）、SCIM（bearer 令牌）、Vault（后端 URL、认证方法）、预算（默认限制、告警阈值）
- **API 面**：新增 `/auth/sso/{provider}/login`、`/auth/sso/{provider}/callback`、`/scim/v2/*`、`/api/budgets/*`、`/api/vault/secrets/*` 端点
