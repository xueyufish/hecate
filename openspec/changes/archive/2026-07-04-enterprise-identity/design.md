## Context — 背景

Hecate 的 Platform SPI 建立了 AuthProviderABC（JWT + APIKey 内置）、ChannelABC 和 i18n SPI。现有的认证系统使用冻结的 AuthContext 数据类，携带 `user_id`、`org_id`、`workspace_id`、`role`、`auth_method` 和 `api_key_scope`。UserModel 已有为外部身份提供者链接保留的 `sso_id` 字段。

QuotaService 支持 `resource_type="cost"`，具有 `workspace` 和 `api_key` 作用域、硬/软限制和基于窗口的执行（rolling_minute、daily、monthly）。CostService 提供定价 CRUD 和来自 TraceModel 令牌使用数据的成本聚合。AlertService 支持基于规则的告警，具有触发/已解决状态。

所有密钥当前通过 pydantic-settings Settings 类从环境变量加载。没有密钥提供者抽象。

企业客户需要：（1）通过其现有 IdP（Azure AD、Okta、LDAP 目录）的单点登录（SSO），（2）通过 SCIM 2.0 的自动化用户配置，（3）具有成本预测的预算控制，以及（4）通过 HashiCorp Vault 或云原生密钥管理器进行的集中式密钥管理。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 实现 OIDC、SAML 和 LDAP 认证作为 AuthProviderABC 插件
- 在首次 SSO 登录时支持 JIT 用户配置（将外部身份映射到本地 UserModel，通过 sso_id）
- 暴露符合 RFC 7643/7644 的 SCIM 2.0 端点，用于 Azure AD / Okta 目录同步
- 添加按组织和按 Agent 的预算执行，带预测和计费报告
- 定义 SecretProviderABC，附带 HashiCorp Vault、AWS Secrets Manager 和 Azure Key Vault 后端
- 所有新的认证提供者通过现有的 Plugin SPI / AuthProviderABC 模式注册
- 向后兼容：现有的 JWT/APIKey 认证流程不变

**非目标：**
- 多因素认证（MFA/2FA）— 独立功能
- 社交登录（Google/Facebook/GitHub 消费级 OAuth）— 仅企业 IdP
- 生物识别认证
- 完整 IdP 反向代理模式（Hecate 作为 SAML IdP，而非仅 SP）
- 基于预算的模型路由（预算低时路由到更便宜的模型）— 未来增强
- 数据库中静态密钥加密（FERNET_KEY 已处理 PII 加密）
- SCIM 出站同步（Hecate 将用户推送到外部 IdP）— 仅入站

## Decisions — 决策

### Decision 1: 通过 Authlib 实现 OIDC（非自定义实现）

**选择**：使用 `authlib` 库进行 OIDC/OAuth 2.0 客户端。

**理由**：Authlib 是 Python OAuth/OIDC 的事实标准，支持异步，处理令牌刷新、PKCE 和发现文档。构建自定义 OIDC 客户端需要实现 JWK 验证、发现解析、令牌交换和刷新逻辑——Authlib 已全部解决。

**备选方案**：
- `itsdangerous` + 手动 JWT 验证 — 过于底层，无发现支持
- `oauthlib` — 维护较少，异步支持较弱
- `authlib.integrations.starlette_client` — 原生 FastAPI/Starlette 集成

### Decision 2: 通过 python3-saml 适配器实现 SAML（非直接使用 pysaml2）

**选择**：使用 `python3-saml`（OneLogin 的 SAML 工具包）包装在异步兼容适配器中。

**理由**：python3-saml 处理 XML 签名、证书验证、ACS 端点处理和 IdP 元数据解析。它是 Python 中使用最广泛的 SAML 库。直接使用 pysaml2 级别较低且需要更多样板代码。该库是同步的，因此在异步兼容性方面我们将其包装在 `run_in_executor` 中。

**备选方案**：
- `pysaml2` — 更灵活但配置更复杂
- `mammoth-saml` — 仅 Laravel
- 自定义 XML 签名 — 安全风险

### Decision 3: 通过 ldap3 使用 asyncio 传输实现 LDAP

**选择**：使用 `ldap3` 库配合 `asyncio` 事件循环。

**理由**：ldap3 是标准的 Python LDAP 库，支持连接池、通过 `asyncio.get_event_loop()` 的异步操作，以及所有 LDAP 服务器类型（Active Directory、OpenLDAP、FreeIPA）。搜索过滤器语法清晰映射到 LDAP 查询。

**备选方案**：
- `ldaptor` — 过时，异步支持差
- `aioldap` — 已废弃

### Decision 4: 通过 scim2-models（原生 Pydantic v2）实现 SCIM 2.0

**选择**：使用 `scim2-models` 进行 SCIM 模式定义、验证和过滤器解析。

**理由**：scim2-models 为 User、Group、ListResponse、PatchOp 和 ServiceProviderConfig 提供完整的 Pydantic v2 模型。上下文感知序列化处理请求/响应差异。内置 ETag 支持。通过 `Annotated[User, Context.RESOURCE_CREATION_REQUEST]` 实现 FastAPI 集成。这避免了手工制作 SCIM JSON 模式和过滤器解析器。

**备选方案**：
- 自定义 Pydantic 模型 — 重复造轮子，RFC 不合规风险高
- `django-scim2` — 特定于 Django，不兼容 FastAPI
- `scim2-filter-parser` — 仅过滤器解析器，无模型

### Decision 5: 预算扩展 QuotaModel，而非新建表

**选择**：使用 `ORG` 和 `AGENT` 值扩展现有的 QuotaScope 枚举。为预测预测和计费添加 BudgetForecastModel。重用 QuotaService 进行执行。

**理由**：QuotaModel 已支持 `resource_type="cost"`、硬/软限制、窗口类型和执行模式。添加 `org` 和 `agent` 作用域是一个两行枚举更改。预算预测（基于历史趋势预测未来支出）是一个新模型，因为它需要每日快照。计费报告是 CostService 数据的只读视图。

**备选方案**：
- 单独的 BudgetModel 表 — 重复 QuotaModel，导致使用混乱
- 从 TraceModel 实时计算预测 — 大数据集成本高，无历史快照

### Decision 6: SecretProviderABC 遵循 AuthProviderABC 模式

**选择**：在 `src/hecate/vault/provider.py` 中定义 `SecretProviderABC`，包含 `name`、`description`、`get_secret(path) → str`、`get_dynamic_credentials(role) → dict` 抽象方法。内置提供者：HashiCorpVaultProvider、AWSSecretsManagerProvider、AzureKeyVaultProvider。

**理由**：遵循与 AuthProviderABC 和 ChannelABC 相同的 ABC + 内置提供者模式。vault 解析器按优先级顺序遍历已注册的提供者。动态凭证（Vault 数据库引擎、AWS STS）返回替换静态 API 密钥的短期凭证。

**备选方案**：
- 仅 Settings 的密钥（当前）— 无动态凭证，无集中轮换
- 外部 sidecar（Vault Agent）— 基础设施复杂性，非自包含

### Decision 7: 通过 JIT（即时）实现 SSO 用户配置

**选择**：首次成功 SSO 认证时，自动创建 UserModel，`sso_id` 设置为外部身份提供者的 subject 声明。无需预注册。

**理由**：JIT 配置是企业 SSO 的标准模式。用户通过其 IdP 认证，Hecate 在首次登录时创建本地用户记录，`hashed_password` 设置为随机值（SSO 用户从不使用密码认证）。AuthContext 中的 `auth_method` 设置为 `"sso"`。

**备选方案**：
- 管理员在 SSO 前手动创建用户 — 操作负担
- 仅 SCIM 配置 — 需要先设置 SCIM 才能使用 SSO（并非所有 IdP 支持 SCIM）

### Decision 8: SCIM 取消配置 = 软删除（active=false）

**选择**：SCIM DELETE 设置 `UserModel.active = False`（新字段）。`active=False` 的用户不能认证。不自动执行硬删除。

**理由**：软删除保留审计跟踪，允许在 IdP 重新分配用户时重新激活，并且是 Azure AD 和 Okta 文档推荐的模式。向 UserModel 添加 `active` 布尔字段（默认为 True）。

**备选方案**：
- SCIM DELETE 时硬删除 — 丢失审计跟踪，破坏外键引用
- 按 IdP 可配置 — 初始版本过度工程

## Risks / Trade-offs — 风险 / 权衡

- **[SAML XML 签名验证]** → 使用 python3-saml 的内置签名验证；永不禁用；添加带签名断言的集成测试
- **[配置中的 LDAP 凭据]** → 将 LDAP 绑定 DN/密码存储在 SecretProvider 中（非纯文本 Settings）；如果未配置 vault，则回退到环境变量
- **[SCIM 过滤器注入]** → 使用 scim2-models 解析器（非字符串插值）；验证所有过滤器输入
- **[Vault 可用性]** → 如果 vault 不可达，以 TTL 回退到缓存的密钥；记录警告；绝不在 vault 连接失败时崩溃
- **[预算执行延迟]** → 配额检查已在中间件中（快速）；预测计算是后台任务，不在请求路径中
- **[SCIM 令牌安全]** → SCIM 端点使用单独的 bearer 令牌（非 JWT）；令牌存储在 Settings/SecretProvider 中；对 SCIM 端点限速
- **[JIT 配置数据质量]** → IdP 可能发送不完整的用户数据；仅映射 `email`、`display_name`、`given_name`、`family_name`；对缺失字段记录警告

## Migration Plan — 迁移计划

1. **阶段 1：用户模型扩展** — 向 UserModel 添加 `active`、`external_id`、`display_name`、`given_name`、`family_name` 字段。Alembic 迁移。所有现有用户获得 `active=True`。
2. **阶段 2：SSO 提供者** — 添加 OIDC/SAML/LDAP 提供者。新增 `/auth/sso/{provider}/login` 和 `/auth/sso/{provider}/callback` 端点。现有的 JWT/APIKey 认证不变。
3. **阶段 3：SCIM 端点** — 添加 `/scim/v2/*` 路由。单独的 SCIM bearer 令牌认证。不影响现有认证。
4. **阶段 4：预算扩展** — 扩展 QuotaScope 枚举（新增式，向后兼容）。添加 BudgetForecastModel。新增预算 API 端点。
5. **阶段 5：Vault 集成** — 添加 SecretProviderABC。保留 Settings 回退。无需迁移（Settings 仍然有效）。

**回滚**：每个阶段独立。SSO 提供者可通过从认证解析器中移除来禁用。SCIM 端点可卸载。预算作用域是新增的。Vault 可回退到 Settings。

## Open Questions — 开放问题

- SSO 提供者是否应支持**多租户 IdP 配置**（每个工作区不同的 OIDC 客户端）？初始实现：仅平台级配置，按工作区 IdP 配置是未来增强。
- 预算预测应使用**简单线性投影**还是**ARIMA/时间序列模型**？初始：简单线性（平均每日支出 × 剩余天数）。高级预测是未来工作。
- vault 密钥应**缓存在内存中**还是始终按需获取？初始：具有可配置 TTL（默认 5 分钟）的缓存。动态凭证从不缓存。
