## ADDED Requirements — 新增需求

### Requirement: SecretProviderABC 抽象接口 — SecretProviderABC abstract interface
系统应在 `vault/provider.py` 中定义 `SecretProviderABC`，包含 `name` 和 `description` 属性，以及抽象方法 `get_secret(path: str) -> str`、`get_dynamic_credentials(role: str) -> dict` 和 `health_check() -> bool`。

#### Scenario: SecretProviderABC 是抽象的 — SecretProviderABC is abstract
- **WHEN** 代码尝试直接实例化 `SecretProviderABC()`
- **THEN** 应抛出 `TypeError`

#### Scenario: 具体子类实现所有方法 — Concrete subclass implements all methods
- **WHEN** SecretProviderABC 的子类实现了 `name`、`description`、`get_secret`、`get_dynamic_credentials` 和 `health_check`
- **THEN** 该子类应可实例化

### Requirement: HashiCorpVaultProvider 内置 — HashiCorpVaultProvider built-in
系统应在 `vault/hcvault_provider.py` 中定义 `HashiCorpVaultProvider(SecretProviderABC)`，使用 `hvac` 库集成 HashiCorp Vault。

#### Scenario: Vault 提供者初始化 — Vault provider initialization
- **WHEN** 使用 `vault_url`、`vault_token`（或 `vault_role_id` + `vault_secret_id` 用于 AppRole）和 `mount_point` 配置创建 HashiCorpVaultProvider
- **THEN** 提供者应初始化 `hvac.Client` 并通过 `sys.health` 验证连接

#### Scenario: 读取静态密钥 — Read static secret
- **WHEN** 调用 `get_secret("secret/data/myapp/api-key")`
- **THEN** 提供者应从 Vault KV v2 引擎读取并返回密钥值

#### Scenario: 获取动态数据库凭据 — Get dynamic database credentials
- **WHEN** 调用 `get_dynamic_credentials("database/roles/myapp-readonly")`
- **THEN** 提供者应从 Vault 数据库引擎请求动态凭据，并返回 `{"username": "...", "password": "...", "lease_id": "...", "lease_duration": 3600}`

#### Scenario: Vault 不可达回退 — Vault unreachable fallback
- **WHEN** Vault 不可达且 `VAULT_FALLBACK_TO_SETTINGS=True`
- **THEN** 提供者应记录警告并返回 None（允许 Settings 回退）
- **AND** 如果 `VAULT_FALLBACK_TO_SETTINGS=False`，应抛出 `VaultConnectionError`

### Requirement: AWSSecretsManagerProvider 内置 — AWSSecretsManagerProvider built-in
系统应在 `vault/aws_provider.py` 中定义 `AWSSecretsManagerProvider(SecretProviderABC)`，使用 `aiobotocore` 进行异步操作，集成 AWS Secrets Manager。

#### Scenario: AWS 提供者初始化 — AWS provider initialization
- **WHEN** 使用 `region_name`、`access_key_id`（可选，回退到 IAM 角色）和 `secret_access_key`（可选）创建 AWSSecretsManagerProvider
- **THEN** 提供者应初始化 aiobotocore 会话

#### Scenario: 从 AWS 读取密钥 — Read secret from AWS
- **WHEN** 调用 `get_secret("myapp/api-key")`
- **THEN** 提供者应调用 `secretsmanager:GetSecretValue` 并返回密钥字符串

#### Scenario: 获取动态 STS 凭据 — Get dynamic STS credentials
- **WHEN** 调用 `get_dynamic_credentials("iam-role/myapp-agent")`
- **THEN** 提供者应调用 STS AssumeRole 并返回带过期时间的临时凭据

### Requirement: AzureKeyVaultProvider 内置 — AzureKeyVaultProvider built-in
系统应在 `vault/azure_provider.py` 中定义 `AzureKeyVaultProvider(SecretProviderABC)`，使用 `azure-keyvault-secrets` 配合 `DefaultAzureCredential` 集成 Azure Key Vault。

#### Scenario: Azure 提供者初始化 — Azure provider initialization
- **WHEN** 使用 `vault_url` 创建 AzureKeyVaultProvider（例如 `https://myvault.vault.azure.net`）
- **THEN** 提供者应使用 `DefaultAzureCredential` 进行认证（支持托管身份、CLI、环境）

#### Scenario: 从 Azure Key Vault 读取密钥 — Read secret from Azure Key Vault
- **WHEN** 调用 `get_secret("api-key")`
- **THEN** 提供者应调用 `SecretClient.get_secret` 并返回密钥值

### Requirement: 带缓存的密钥解析器 — Secret resolver with caching
系统应在 `vault/resolver.py` 中定义 `resolve_secret(path: str) -> str` 函数，按优先级顺序遍历已注册的 SecretProvider，以可配置的 TTL 缓存结果，并回退到 Settings 环境变量。

#### Scenario: 从 vault 解析密钥 — Resolve secret from vault
- **WHEN** 调用 `resolve_secret("database/url")` 且 vault 已配置
- **THEN** 解析器应检查内存缓存（TTL 来自 `VAULT_CACHE_TTL`，默认 300 秒）
- **AND** 如果缓存未命中，按优先级顺序遍历提供者并返回第一个非 None 结果

#### Scenario: 回退到 Settings — Fall back to Settings
- **WHEN** 调用 `resolve_secret("database/url")`，未注册 vault 提供者，且 Settings 中存在 `DATABASE_URL`
- **THEN** 解析器应返回 Settings 值

#### Scenario: 缓存过期 — Cache expiry
- **WHEN** 缓存密钥的 TTL 已过期
- **THEN** 下一次 `resolve_secret` 调用应从提供者重新获取

#### Scenario: 动态凭据从不缓存 — Dynamic credentials never cached
- **WHEN** 调用 `resolve_dynamic_credentials(role)`
- **THEN** 解析器应始终获取新凭据（不缓存），因为它们具有有限的租期

### Requirement: SecretProvider 注册 — SecretProvider registration
系统应在 `vault/registration.py` 中提供 `register_secret_providers(registry: PluginRegistry)`，将配置的 SecretProvider 实例注册为 Plugin SPI 条目。

#### Scenario: Vault 提供者已注册 — Vault provider registered
- **WHEN** Settings 配置了 `VAULT_URL` 和 `VAULT_TOKEN`
- **THEN** `register_secret_providers` 应创建 HashiCorpVaultProvider 并注册到 PluginRegistry

#### Scenario: AWS 提供者已注册 — AWS provider registered
- **WHEN** Settings 配置了 `AWS_SECRETS_REGION`
- **THEN** `register_secret_providers` 应创建 AWSSecretsManagerProvider 并注册

#### Scenario: 未配置 vault — No vault configured
- **WHEN** 没有 vault 设置
- **THEN** 不应注册提供者，`resolve_secret` 应回退到 Settings

### Requirement: Settings 中的 Vault 配置 — Vault configuration in Settings
系统应向 Settings 类添加 vault 集成配置。

#### Scenario: HashiCorp Vault 设置 — HashiCorp Vault settings
- **WHEN** Settings 包含 `VAULT_URL`、`VAULT_TOKEN`（或 `VAULT_ROLE_ID` + `VAULT_SECRET_ID`）、`VAULT_MOUNT_POINT`（默认 "secret"）
- **THEN** HashiCorpVaultProvider 应被初始化

#### Scenario: AWS Secrets Manager 设置 — AWS Secrets Manager settings
- **WHEN** Settings 包含 `AWS_SECRETS_REGION`、`AWS_SECRETS_ACCESS_KEY_ID`（可选）、`AWS_SECRETS_SECRET_ACCESS_KEY`（可选）
- **THEN** AWSSecretsManagerProvider 应被初始化

#### Scenario: Azure Key Vault 设置 — Azure Key Vault settings
- **WHEN** Settings 包含 `AZURE_KEYVAULT_URL`
- **THEN** AzureKeyVaultProvider 应被初始化

#### Scenario: 缓存 TTL 配置 — Cache TTL configuration
- **WHEN** Settings 包含 `VAULT_CACHE_TTL`（默认 300）
- **THEN** 密钥解析器应以该时长（秒）缓存静态密钥

#### Scenario: 回退开关 — Fallback toggle
- **WHEN** Settings 包含 `VAULT_FALLBACK_TO_SETTINGS`（默认为 True）
- **THEN** 当 vault 不可达时，解析器应回退到 Settings
