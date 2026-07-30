## ADDED Requirements — 新增需求

### Requirement: API keys are encrypted at rest — API key 静态加密
The system SHALL encrypt API keys using Fernet symmetric encryption before storing in the database. Decryption happens transparently when using the keys.

系统应在存储到数据库之前使用 Fernet 对称加密对 API key 进行加密。使用 key 时进行透明的解密。

#### Scenario: Encrypt API key on save — 保存时加密 API key
- **WHEN** a provider is created with api_key="sk-abc123"
- **THEN** the database stores the Fernet-encrypted version of the key
- **当**使用 api_key="sk-abc123" 创建 provider
- **则**数据库存储该 key 的 Fernet 加密版本

#### Scenario: Decrypt API key on use — 使用时解密 API key
- **WHEN** the system needs to call a model through a provider
- **THEN** it decrypts the stored API key and passes the plaintext to LiteLLM
- **当**系统需要通过 provider 调用模型
- **则**解密存储的 API key 并将明文传递给 LiteLLM

### Requirement: Fernet key from environment — 从环境变量获取 Fernet key
The encryption key SHALL be read from the FERNET_KEY environment variable. If not set, the system stores API keys in plaintext for development convenience.

加密密钥应从 FERNET_KEY 环境变量中读取。如果未设置，系统以明文形式存储 API key 以便于开发。

#### Scenario: Production with FERNET_KEY set — 生产环境设置了 FERNET_KEY
- **WHEN** FERNET_KEY environment variable is set
- **THEN** all API keys are encrypted with Fernet before storage
- **当**设置了 FERNET_KEY 环境变量
- **则**所有 API key 在存储前都使用 Fernet 加密

#### Scenario: Development without FERNET_KEY — 开发环境未设置 FERNET_KEY
- **WHEN** FERNET_KEY environment variable is not set
- **THEN** API keys are stored in plaintext (backward compatible)
- **当**未设置 FERNET_KEY 环境变量
- **则**API key 以明文形式存储（向后兼容）
