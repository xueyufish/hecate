## 1. 用户模型扩展（scim-provisioning 规范）

- [x] 1.1 向 `src/hecate/models/user.py` 的 UserModel 添加 `external_id`（String 255，可空，已索引）、`display_name`（String 255，可空）、`given_name`（String 128，可空）、`family_name`（String 128，可空）、`active`（Boolean，默认 True）字段
- [x] 1.2 更新 UserReadSchema 以包含新字段
- [x] 1.3 为新用户字段创建 Alembic 迁移（所有现有用户获得 `active=True`）
- [x] 1.4 更新认证解析器和所有认证提供者，拒绝 `active=False` 的用户

## 2. SSO 认证提供者 — OIDC（sso-auth 规范）

- [x] 2.1 在 `pyproject.toml` 的 `[security]` 可选依赖组中添加 `authlib`
- [x] 2.2 向 `src/hecate/core/config.py` 添加 OIDC 设置：`SSO_OIDC_CLIENT_ID`、`SSO_OIDC_CLIENT_SECRET`、`SSO_OIDC_DISCOVERY_URL`、`SSO_OIDC_SCOPE`（默认 "openid profile email"）
- [x] 2.3 创建 `src/hecate/auth/oidc_provider.py`，包含 `OIDCAuthProvider(AuthProviderABC)`，使用 Authlib Starlette 集成——授权码流程带 PKCE、发现文档解析、userinfo 端点查询
- [x] 2.4 添加 JIT 配置逻辑：首次 OIDC 登录时，创建 UserModel，`sso_id=sub`、`email`、`display_name`、随机 `hashed_password`、`active=True`
- [x] 2.5 在 `src/hecate/auth/sso_routes.py` 中创建 SSO 登录/回调路由：`GET /auth/sso/oidc/login`（重定向到 IdP）、`GET /auth/sso/oidc/callback`（code 交换 + JWT 签发）
- [x] 2.6 当存在 OIDC 设置时，在 `src/hecate/auth/registration.py` 中注册 OIDCAuthProvider
- [x] 2.7 创建 `tests/test_auth/test_oidc_provider.py` — 测试提供者初始化、JIT 配置逻辑、state 验证、错误处理

## 3. SSO 认证提供者 — SAML（sso-auth 规范）

- [x] 3.1 在 `pyproject.toml` 的 `[security]` 可选依赖组中添加 `python3-saml`
- [x] 3.2 向配置添加 SAML 设置：`SSO_SAML_SP_ENTITY_ID`、`SSO_SAML_SP_ACS_URL`、`SSO_SAML_IDP_ENTITY_ID`、`SSO_SAML_IDP_SSO_URL`、`SSO_SAML_IDP_X509_CERT`
- [x] 3.3 创建 `src/hecate/auth/saml_provider.py`，包含 `SAMLAuthProvider(AuthProviderABC)`，包装 python3-saml——AuthnRequest 生成、ACS 断言解析、签名验证
- [x] 3.4 向 `src/hecate/auth/sso_routes.py` 添加 SAML 路由：`GET /auth/sso/saml/login`、`POST /auth/sso/saml/acs`
- [x] 3.5 当存在 SAML 设置时，在 registration 中注册 SAMLAuthProvider
- [x] 3.6 创建 `tests/test_auth/test_saml_provider.py` — 测试 AuthnRequest 生成、断言验证、JIT 配置

## 4. SSO 认证提供者 — LDAP（sso-auth 规范）

- [x] 4.1 在 `pyproject.toml` 的 `[security]` 可选依赖组中添加 `ldap3`
- [x] 4.2 向配置添加 LDAP 设置：`SSO_LDAP_SERVER_URL`、`SSO_LDAP_BASE_DN`、`SSO_LDAP_BIND_DN`、`SSO_LDAP_BIND_PASSWORD`、`SSO_LDAP_SEARCH_FILTER`（默认 "(uid={})"）、`SSO_LDAP_USE_SSL`（默认 True）
- [x] 4.3 创建 `src/hecate/auth/ldap_provider.py`，包含 `LDAPAuthProvider(AuthProviderABC)`，使用 ldap3 asyncio 传输——通过过滤器搜索用户、绑定认证、连接错误处理
- [x] 4.4 添加 LDAP JIT 配置：首次成功 LDAP 绑定时创建 UserModel，`sso_id=username`、`email` 来自 LDAP mail 属性
- [x] 4.5 当存在 LDAP 设置时，在 registration 中注册 LDAPAuthProvider
- [x] 4.6 创建 `tests/test_auth/test_ldap_provider.py` — 测试绑定成功/失败、服务器不可达处理、JIT 配置

## 5. AuthContext 扩展（sso-auth 规范）

- [x] 5.1 更新 `src/hecate/core/auth_context.py` 中 AuthContext 的 `auth_method` 类型为 `Literal["jwt", "api_key", "sso", "ldap"]`
- [x] 5.2 更新 `src/hecate/auth/resolver.py` 以在解析器链中处理 SSO 和 LDAP 认证方法
- [x] 5.3 更新 `src/hecate/auth/registration.py` 以注册所有已配置的 SSO 提供者
- [x] 5.4 在 `src/hecate/main.py` 中注册 SSO 路由

## 6. SCIM 2.0 核心（scim-provisioning 规范）

- [x] 6.1 在 `pyproject.toml` 的 `[security]` 可选依赖组中添加 `scim2-models`
- [x] 6.2 向配置添加 SCIM 设置：`SCIM_BEARER_TOKEN`、`SCIM_ENABLED`（默认 False）
- [x] 6.3 创建 `src/hecate/scim/__init__.py`，包含公共导出
- [x] 6.4 创建 `src/hecate/scim/models.py` — SCIM User/Group 在 scim2-models 和 UserModel 之间的映射器，包含属性映射函数（to_scim_user、from_scim_user）
- [x] 6.5 创建 `src/hecate/scim/auth.py` — 用于 FastAPI 的 SCIM bearer 令牌认证依赖
- [x] 6.6 创建 `src/hecate/scim/filter_parser.py` — 将 SCIM 过滤器语法（eq、co、sw、and）转换为 UserModel 上的 SQLAlchemy 查询

## 7. SCIM 2.0 用户端点（scim-provisioning 规范）

- [x] 7.1 创建 `src/hecate/scim/users.py`，包含 SCIM 用户端点：POST /scim/v2/Users（创建）、GET /scim/v2/Users（列表+过滤+分页）、GET /scim/v2/Users/{id}、PUT /scim/v2/Users/{id}（替换）、PATCH /scim/v2/Users/{id}（部分更新 + active=false 取消配置）、DELETE /scim/v2/Users/{id}（软删除）
- [x] 7.2 实现 SCIM 错误响应格式（RFC 7644 §3.12），包含 `schemas`、`status`、`scimType`、`detail` 字段
- [x] 7.3 实现分页，使用 `startIndex`（从 1 开始）、`count`、`totalResults`，符合 RFC 7644 §3.4.2.4
- [x] 7.4 实现乐观并发的 ETag 支持（PUT/PATCH/DELETE 上的 If-Match 头）

## 8. SCIM 2.0 组 + 发现端点（scim-provisioning 规范）

- [x] 8.1 创建 `src/hecate/scim/groups.py`，包含 SCIM 组端点：POST、GET（列表）、GET（单个）、PATCH（成员关系）、DELETE——映射到工作区团队/角色
- [x] 8.2 创建 `src/hecate/scim/discovery.py` — ServiceProviderConfig、Schemas、ResourceTypes 端点
- [x] 8.3 在 `src/hecate/main.py` 中注册 SCIM 路由器（当 SCIM 启用时）
- [x] 8.4 创建 `tests/test_scim/test_users.py` — 测试 CRUD、过滤器、分页、取消配置、错误响应
- [x] 8.5 创建 `tests/test_scim/test_groups.py` — 测试组 CRUD 和成员关系同步
- [x] 8.6 创建 `tests/test_scim/test_discovery.py` — 测试 ServiceProviderConfig、Schemas、ResourceTypes 响应

## 9. 预算 — 配额扩展（budget-management 规范）

- [x] 9.1 向 `src/hecate/models/quota.py` 的 `QuotaScope` 枚举添加 `ORG = "org"` 和 `AGENT = "agent"`
- [x] 9.2 更新 `src/hecate/services/quota_service.py` — `check_quota` 和 `record_usage` 方法已通用接受 scope/scope_id；验证 org/agent 作用域端到端工作
- [x] 9.3 添加成本记录钩子：LLM 调用后，在 LLMWorker 或 WorkflowExecutionService 中为 org、workspace 和 agent 作用域调用 `QuotaService.record_usage(resource_type="cost", ...)`

## 10. 预算 — 预测 + 服务（budget-management 规范）

- [x] 10.1 创建 `src/hecate/models/budget.py`，包含 `BudgetForecastModel(BaseModel)` — 字段：`scope`（String 16）、`scope_id`（UUID）、`date`（Date）、`daily_cost`（Float）、`daily_input_tokens`（Integer）、`daily_output_tokens`（Integer）、`workspace_id`（UUID）
- [x] 10.2 为 BudgetForecastModel 表创建 Alembic 迁移
- [x] 10.3 创建 `src/hecate/budget/__init__.py`，包含公共导出
- [x] 10.4 创建 `src/hecate/budget/budget_service.py` — BudgetService，包含：`get_utilization(scope, scope_id)`、使用 7 天平均值的 `forecast_remaining(scope, scope_id)`、委托给 CostService 的 `create_chargeback(scope, scope_id, group_by, start, end)`
- [x] 10.5 添加每日预测快照的计划任务——使用 CostService 的每日成本记录每个组织/工作区的 BudgetForecastModel
- [x] 10.6 创建 `tests/test_budget/test_budget_service.py` — 测试利用率、预测投影、计费分组

## 11. 预算 — API 端点（budget-management 规范）

- [x] 11.1 创建 `src/hecate/api/management/budget.py` — REST 端点：POST /api/budgets、GET /api/budgets、PUT /api/budgets/{id}、DELETE /api/budgets/{id}、GET /api/budgets/{id}/status（含预测）、GET /api/budgets/chargeback
- [x] 11.2 在 `src/hecate/main.py` 中注册预算路由器
- [x] 11.3 创建 `tests/test_api/test_budget_api.py` — 测试预算 CRUD、带预测的状态、计费报告

## 12. Vault — SecretProviderABC + 内置实现（vault-integration 规范）

- [x] 12.1 创建 `src/hecate/vault/__init__.py`，包含公共导出
- [x] 12.2 创建 `src/hecate/vault/provider.py` — `SecretProviderABC`，包含 `name`、`description` 属性，`get_secret(path)`、`get_dynamic_credentials(role)`、`health_check()` 抽象方法
- [x] 12.3 在 `[security]` 可选依赖组中添加 `hvac`
- [x] 12.4 创建 `src/hecate/vault/hcvault_provider.py` — HashiCorpVaultProvider，使用 hvac.Client、KV v2 读取、数据库引擎动态凭据、健康检查、AppRole + 令牌认证支持
- [x] 12.5 在 `[security]` 可选依赖组中添加 `aiobotocore`
- [x] 12.6 创建 `src/hecate/vault/aws_provider.py` — AWSSecretsManagerProvider，使用 aiobotocore、GetSecretValue、STS AssumeRole 获取动态凭据
- [x] 12.7 在 `[security]` 可选依赖组中添加 `azure-keyvault-secrets` + `azure-identity`
- [x] 12.8 创建 `src/hecate/vault/azure_provider.py` — AzureKeyVaultProvider，使用 SecretClient + DefaultAzureCredential

## 13. Vault — 解析器 + 注册（vault-integration 规范）

- [x] 13.1 向 `src/hecate/core/config.py` 添加 vault 设置：`VAULT_URL`、`VAULT_TOKEN`（或 `VAULT_ROLE_ID`+`VAULT_SECRET_ID`）、`VAULT_MOUNT_POINT`（默认 "secret"）、`AWS_SECRETS_REGION`、`AWS_SECRETS_ACCESS_KEY_ID`、`AWS_SECRETS_SECRET_ACCESS_KEY`、`AZURE_KEYVAULT_URL`、`VAULT_CACHE_TTL`（默认 300）、`VAULT_FALLBACK_TO_SETTINGS`（默认 True）
- [x] 13.2 创建 `src/hecate/vault/resolver.py` — `resolve_secret(path)`，带提供者迭代、内存缓存带 TTL、Settings 回退；`resolve_dynamic_credentials(role)` 不缓存
- [x] 13.3 创建 `src/hecate/vault/registration.py` — `register_secret_providers(registry)`，创建并注册已配置的提供者为 Plugin SPI 条目
- [x] 13.4 在 `src/hecate/main.py` 启动中注册 vault 初始化
- [x] 13.5 创建 `tests/test_vault/test_provider.py` — 测试 SecretProviderABC 抽象性、HashiCorpVaultProvider 初始化（mock hvac）、健康检查
- [x] 13.6 创建 `tests/test_vault/test_resolver.py` — 测试带缓存的密钥解析、回退到 Settings、不缓存的动态凭据

## 14. 集成和最终验证

- [x] 14.1 更新 `src/hecate/auth/__init__.py` 以导出新的 SSO 提供者（OIDCAuthProvider、SAMLAuthProvider、LDAPAuthProvider）
- [x] 14.2 更新 `src/hecate/plugin/spi/__init__.py` 以导出 SecretProviderABC
- [x] 14.3 运行完整验证：`ruff check src/hecate/ tests/ && ruff format --check src/ tests/ && mypy src/ && python -m pytest tests/ -q`
- [x] 14.4 修复任何 lint、类型或测试失败
