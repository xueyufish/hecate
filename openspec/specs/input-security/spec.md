## ADDED Requirements

### Requirement: InputSecurityHook implements PreLLMHook
The `InputSecurityHook` SHALL implement the `PreLLMHook` ABC, providing prompt injection detection, PII anonymization, and harmful content filtering for user messages before they reach the LLM.

#### Scenario: Clean messages pass through
- **WHEN** `on_pre_llm_call(messages, model, tools)` is called with messages containing no PII or injection patterns
- **THEN** it SHALL return `GuardrailResult(action=GuardrailAction.ALLOW)`

#### Scenario: PII detected in messages
- **WHEN** messages contain PII (email, phone, SSN, credit card, IP address) and `input_security.pii_entities` includes the detected type
- **THEN** it SHALL anonymize PII in messages and return `GuardrailResult(action=GuardrailAction.SANITIZE, modified_data={"messages": <anonymized_messages>})`

#### Scenario: Prompt injection detected
- **WHEN** the LLMGuardScanner PromptInjection scanner detects a risk score above the configured threshold
- **THEN** it SHALL return `GuardrailResult(action=GuardrailAction.BLOCK, reason="Prompt injection detected: ...")` when `input_security.block_on_injection` is True
- **THEN** it SHALL return `GuardrailResult(action=GuardrailAction.SANITIZE, reason="Prompt injection warning", modified_data={"messages": <messages_with_warning>})` when `input_security.block_on_injection` is False

#### Scenario: Secrets detected in messages
- **WHEN** the LLMGuardScanner Secrets scanner detects API keys, tokens, or credentials
- **THEN** it SHALL return `GuardrailResult(action=GuardrailAction.BLOCK, reason="Secrets detected in input")`

#### Scenario: Security disabled for agent
- **WHEN** `input_security.enabled` is False or guardrail_config is None
- **THEN** it SHALL return `GuardrailResult(action=GuardrailAction.ALLOW)` without scanning

### Requirement: InputSecurityHook preserves PII mappings
The `InputSecurityHook` SHALL maintain a session-scoped mapping of anonymized PII placeholders to original values, enabling downstream deanonymization in OutputSecurityHook.

#### Scenario: Mapping stored for session
- **WHEN** PII is anonymized in messages for a session
- **THEN** the placeholder-to-original mapping SHALL be stored in the execution context under `_pii_mappings` key for use by OutputSecurityHook

#### Scenario: Multiple PII instances of same type
- **WHEN** multiple email addresses are found in messages
- **THEN** each SHALL receive a unique placeholder (`[EMAIL_1]`, `[EMAIL_2]`, etc.) with separate mappings

### Requirement: InputSecurityHook configurable entity types
The `InputSecurityHook` SHALL accept a configurable list of PII entity types to detect, controlled by `guardrail_config.input_security.pii_entities`.

#### Scenario: Custom entity list
- **WHEN** `pii_entities` is set to `["email", "phone"]`
- **THEN** only email and phone PII SHALL be anonymized; SSN, credit card, and IP address SHALL pass through unchanged

#### Scenario: Default entity list
- **WHEN** `pii_entities` is not specified
- **THEN** all supported entity types (email, phone, credit_card, ssn, ip_address) SHALL be detected
