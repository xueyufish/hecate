## ADDED Requirements

### Requirement: SecretProviderABC abstract interface
The system SHALL define `SecretProviderABC` in `vault/provider.py` with `name` and `description` properties, and abstract methods `get_secret(path: str) -> str`, `get_dynamic_credentials(role: str) -> dict`, and `health_check() -> bool`.

#### Scenario: SecretProviderABC is abstract
- **WHEN** code attempts to instantiate `SecretProviderABC()` directly
- **THEN** a `TypeError` SHALL be raised

#### Scenario: Concrete subclass implements all methods
- **WHEN** a subclass of SecretProviderABC implements `name`, `description`, `get_secret`, `get_dynamic_credentials`, and `health_check`
- **THEN** the subclass SHALL be instantiable

### Requirement: HashiCorpVaultProvider built-in
The system SHALL define `HashiCorpVaultProvider(SecretProviderABC)` in `vault/hcvault_provider.py` that integrates with HashiCorp Vault using the `hvac` library.

#### Scenario: Vault provider initialization
- **WHEN** a HashiCorpVaultProvider is created with `vault_url`, `vault_token` (or `vault_role_id` + `vault_secret_id` for AppRole), and `mount_point` configuration
- **THEN** the provider SHALL initialize an `hvac.Client` and verify connectivity via `sys.health`

#### Scenario: Read static secret
- **WHEN** `get_secret("secret/data/myapp/api-key")` is called
- **THEN** the provider SHALL read from Vault KV v2 engine and return the secret value

#### Scenario: Get dynamic database credentials
- **WHEN** `get_dynamic_credentials("database/roles/myapp-readonly")` is called
- **THEN** the provider SHALL request dynamic credentials from Vault database engine and return `{"username": "...", "password": "...", "lease_id": "...", "lease_duration": 3600}`

#### Scenario: Vault unreachable fallback
- **WHEN** Vault is unreachable and `VAULT_FALLBACK_TO_SETTINGS=True`
- **THEN** the provider SHALL log a warning and return None (allowing Settings fallback)
- **AND** if `VAULT_FALLBACK_TO_SETTINGS=False`, SHALL raise `VaultConnectionError`

### Requirement: AWSSecretsManagerProvider built-in
The system SHALL define `AWSSecretsManagerProvider(SecretProviderABC)` in `vault/aws_provider.py` that integrates with AWS Secrets Manager using `aiobotocore` for async operations.

#### Scenario: AWS provider initialization
- **WHEN** an AWSSecretsManagerProvider is created with `region_name`, `access_key_id` (optional, falls back to IAM role), and `secret_access_key` (optional)
- **THEN** the provider SHALL initialize an aiobotocore session

#### Scenario: Read secret from AWS
- **WHEN** `get_secret("myapp/api-key")` is called
- **THEN** the provider SHALL call `secretsmanager:GetSecretValue` and return the secret string

#### Scenario: Get dynamic STS credentials
- **WHEN** `get_dynamic_credentials("iam-role/myapp-agent")` is called
- **THEN** the provider SHALL call STS AssumeRole and return temporary credentials with expiration

### Requirement: AzureKeyVaultProvider built-in
The system SHALL define `AzureKeyVaultProvider(SecretProviderABC)` in `vault/azure_provider.py` that integrates with Azure Key Vault using `azure-keyvault-secrets` with `DefaultAzureCredential`.

#### Scenario: Azure provider initialization
- **WHEN** an AzureKeyVaultProvider is created with `vault_url` (e.g., `https://myvault.vault.azure.net`)
- **THEN** the provider SHALL use `DefaultAzureCredential` for authentication (supports managed identity, CLI, environment)

#### Scenario: Read secret from Azure Key Vault
- **WHEN** `get_secret("api-key")` is called
- **THEN** the provider SHALL call `SecretClient.get_secret` and return the secret value

### Requirement: Secret resolver with caching
The system SHALL define a `resolve_secret(path: str) -> str` function in `vault/resolver.py` that iterates registered SecretProviders in priority order, caches results with configurable TTL, and falls back to Settings environment variables.

#### Scenario: Resolve secret from vault
- **WHEN** `resolve_secret("database/url")` is called and vault is configured
- **THEN** the resolver SHALL check the in-memory cache (TTL from `VAULT_CACHE_TTL`, default 300 seconds)
- **AND** if cache miss, iterate providers in priority order and return the first non-None result

#### Scenario: Fall back to Settings
- **WHEN** `resolve_secret("database/url")` is called, no vault providers are registered, and `DATABASE_URL` exists in Settings
- **THEN** the resolver SHALL return the Settings value

#### Scenario: Cache expiry
- **WHEN** a cached secret's TTL has expired
- **THEN** the next `resolve_secret` call SHALL re-fetch from the provider

#### Scenario: Dynamic credentials never cached
- **WHEN** `resolve_dynamic_credentials(role)` is called
- **THEN** the resolver SHALL always fetch fresh credentials (no caching) because they have limited lease duration

### Requirement: SecretProvider registration
The system SHALL provide `register_secret_providers(registry: PluginRegistry)` in `vault/registration.py` that registers configured SecretProvider instances as Plugin SPI entries.

#### Scenario: Vault provider registered
- **WHEN** Settings has `VAULT_URL` and `VAULT_TOKEN` configured
- **THEN** `register_secret_providers` SHALL create a HashiCorpVaultProvider and register it with the PluginRegistry

#### Scenario: AWS provider registered
- **WHEN** Settings has `AWS_SECRETS_REGION` configured
- **THEN** `register_secret_providers` SHALL create an AWSSecretsManagerProvider and register it

#### Scenario: No vault configured
- **WHEN** no vault settings are present
- **THEN** no providers SHALL be registered and `resolve_secret` SHALL fall back to Settings

### Requirement: Vault configuration in Settings
The system SHALL add vault integration configuration to the Settings class.

#### Scenario: HashiCorp Vault settings
- **WHEN** Settings includes `VAULT_URL`, `VAULT_TOKEN` (or `VAULT_ROLE_ID` + `VAULT_SECRET_ID`), `VAULT_MOUNT_POINT` (default "secret")
- **THEN** the HashiCorpVaultProvider SHALL be initialized

#### Scenario: AWS Secrets Manager settings
- **WHEN** Settings includes `AWS_SECRETS_REGION`, `AWS_SECRETS_ACCESS_KEY_ID` (optional), `AWS_SECRETS_SECRET_ACCESS_KEY` (optional)
- **THEN** the AWSSecretsManagerProvider SHALL be initialized

#### Scenario: Azure Key Vault settings
- **WHEN** Settings includes `AZURE_KEYVAULT_URL`
- **THEN** the AzureKeyVaultProvider SHALL be initialized

#### Scenario: Cache TTL configuration
- **WHEN** Settings includes `VAULT_CACHE_TTL` (default 300)
- **THEN** the secret resolver SHALL cache static secrets for that duration in seconds

#### Scenario: Fallback toggle
- **WHEN** Settings includes `VAULT_FALLBACK_TO_SETTINGS` (default True)
- **THEN** the resolver SHALL fall back to Settings when vault is unreachable
