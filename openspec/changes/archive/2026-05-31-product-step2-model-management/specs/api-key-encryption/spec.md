## ADDED Requirements

### Requirement: API keys are encrypted at rest
The system SHALL encrypt API keys using Fernet symmetric encryption before storing in the database. Decryption happens transparently when using the keys.

#### Scenario: Encrypt API key on save
- **WHEN** a provider is created with api_key="sk-abc123"
- **THEN** the database stores the Fernet-encrypted version of the key

#### Scenario: Decrypt API key on use
- **WHEN** the system needs to call a model through a provider
- **THEN** it decrypts the stored API key and passes the plaintext to LiteLLM

### Requirement: Fernet key from environment
The encryption key SHALL be read from the FERNET_KEY environment variable. If not set, the system stores API keys in plaintext for development convenience.

#### Scenario: Production with FERNET_KEY set
- **WHEN** FERNET_KEY environment variable is set
- **THEN** all API keys are encrypted with Fernet before storage

#### Scenario: Development without FERNET_KEY
- **WHEN** FERNET_KEY environment variable is not set
- **THEN** API keys are stored in plaintext (backward compatible)
