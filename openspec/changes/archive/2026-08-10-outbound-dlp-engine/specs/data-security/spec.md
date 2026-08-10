## MODIFIED Requirements

### Requirement: ToolResultSecurityHook implements PostToolHook
The `ToolResultSecurityHook` SHALL implement the `PostToolHook` ABC, detecting and masking sensitive entities in tool execution results using DLPScanner before they are stored in channels or returned to the LLM.

#### Scenario: Clean tool result passes through
- **WHEN** `on_post_tool_call(name, result, context)` is called with a result containing no sensitive entities
- **THEN** it SHALL return `GuardrailResult(action=GuardrailAction.ALLOW)`

#### Scenario: Sensitive entity detected in tool result
- **WHEN** the tool result string contains PII/secrets patterns and DLPScanner.scan() detects them
- **THEN** the hook SHALL mask sensitive entities in the result and return `GuardrailResult(action=GuardrailAction.SANITIZE, modified_data={"result": <masked_result>})`

#### Scenario: Tool result security disabled
- **WHEN** `data_security.enabled` is False or guardrail_config is None
- **THEN** tool results SHALL pass through without scanning

### Requirement: PII storage mode configuration
The system SHALL support two PII storage modes controlled by `guardrail_config.data_security.pii_storage_mode`.

#### Scenario: mask_only mode (default)
- **WHEN** `pii_storage_mode` is `"mask_only"` or not specified
- **THEN** PII SHALL be replaced with irreversible placeholders before database storage
- **THEN** no original PII values SHALL be persisted

#### Scenario: mask_and_encrypt mode
- **WHEN** `pii_storage_mode` is `"mask_and_encrypt"`
- **THEN** original PII values SHALL be encrypted with Fernet and stored in a `PIIMappingModel` table
- **THEN** each mapping SHALL be keyed by (session_id, placeholder)
- **THEN** encrypted values SHALL be recoverable by authorized components using the Fernet key

#### Scenario: Fernet key not configured
- **WHEN** `pii_storage_mode` is `"mask_and_encrypt"` and `FERNET_KEY` is not set
- **THEN** the system SHALL raise a `ConfigurationError` at hook construction time

### Requirement: PIIMappingModel for encrypted mappings
The system SHALL define `PIIMappingModel` ORM model for storing Fernet-encrypted PII mappings in `mask_and_encrypt` mode.

#### Scenario: Model fields
- **WHEN** `PIIMappingModel` is defined
- **THEN** it SHALL have fields: `id` (UUID PK), `session_id` (UUID, FK to sessions), `placeholder` (str, e.g., "[EMAIL_1]"), `encrypted_value` (bytes, Fernet-encrypted), `pii_type` (str, e.g., "email"), `created_at` (datetime)

#### Scenario: Unique constraint
- **WHEN** a mapping is saved
- **THEN** the combination of (session_id, placeholder) SHALL be unique

### Requirement: PII audit event logging
The system SHALL log PII detection events to the EventStore when `data_security.audit_pii_events` is True.

#### Scenario: PII detected and logged
- **WHEN** PII is detected in any data flow (input, output, tool result) and audit is enabled
- **THEN** an Event SHALL be appended to EventStore with type `PII_DETECTED`, containing pii_type and placeholder count, but NOT the original PII value

#### Scenario: Audit disabled
- **WHEN** `audit_pii_events` is False
- **THEN** no PII detection events SHALL be logged to EventStore

### Requirement: AgentModel guardrail_config column
The `AgentModel` SHALL have a `guardrail_config` JSONB column for per-agent security configuration.

#### Scenario: Column added to AgentModel
- **WHEN** the migration runs
- **THEN** the `agents` table SHALL have a nullable `guardrail_config` JSONB column with default `NULL`

#### Scenario: Agent created with guardrail config
- **WHEN** an agent is created with `guardrail_config` in the request body
- **THEN** the config SHALL be stored in the JSONB column

#### Scenario: Agent created without guardrail config
- **WHEN** an agent is created without `guardrail_config`
- **THEN** the column SHALL be `NULL`, meaning security hooks are disabled for this agent

#### Scenario: Guardrail config updated
- **WHEN** an agent is updated with a new `guardrail_config`
- **THEN** the stored config SHALL be replaced atomically

## ADDED Requirements

### Requirement: ToolResultSecurityHook delegates to DLPScanner
The `ToolResultSecurityHook` SHALL delegate sensitive entity detection to `DLPScanner.scan(direction="tool_output")` instead of the previous hardcoded `PIIAnonymizer.PATTERNS`.

#### Scenario: DLPScanner enabled
- **WHEN** `DLP_ENABLED=True` and DLPScanner is injected
- **THEN** the hook SHALL use DLPScanner.scan() for detection

#### Scenario: DLPScanner disabled
- **WHEN** `DLP_ENABLED=False` or DLPScanner is None
- **THEN** the hook SHALL fall back to PIIAnonymizer (backward compatibility)