## ADDED Requirements

### Requirement: OutputSecurityHook implements PostLLMHook
The `OutputSecurityHook` SHALL implement the `PostLLMHook` ABC, providing output toxicity detection and PII deanonymization for LLM responses.

#### Scenario: Clean response passes through
- **WHEN** `on_post_llm_call(response, messages)` is called with a response containing no toxicity and no PII placeholders
- **THEN** it SHALL return `GuardrailResult(action=GuardrailAction.ALLOW)`

#### Scenario: Toxicity detected in response
- **WHEN** the LLMGuardScanner Toxicity scanner detects a risk score above `output_security.toxicity_threshold`
- **THEN** it SHALL return `GuardrailResult(action=GuardrailAction.BLOCK, reason="Toxic output detected: ...")`

#### Scenario: PII placeholders deanonymized in non-streaming response
- **WHEN** the response contains PII placeholders (e.g., `[EMAIL_1]`) and `output_security.deanonymize` is True
- **THEN** it SHALL replace placeholders with original values from the session PII mappings and return `GuardrailResult(action=GuardrailAction.SANITIZE, modified_data={"response": <deanonymized_response>})`

#### Scenario: Deanonymization disabled
- **WHEN** `output_security.deanonymize` is False
- **THEN** PII placeholders SHALL pass through to the user without replacement

#### Scenario: Security disabled for agent
- **WHEN** `output_security.enabled` is False or guardrail_config is None
- **THEN** it SHALL return `GuardrailResult(action=GuardrailAction.ALLOW)` without scanning

### Requirement: StreamDeanonymizer handles streaming PII
The `StreamDeanonymizer` SHALL buffer streaming tokens to detect and deanonymize complete PII placeholders before emitting them to the user.

#### Scenario: Non-PII tokens emitted immediately
- **WHEN** incoming token does not start with `[` and buffer is empty
- **THEN** the token SHALL be emitted immediately without buffering

#### Scenario: PII placeholder split across tokens
- **WHEN** tokens `["Contact [", "EMAIL_", "1] for help"]` arrive sequentially
- **THEN** the StreamDeanonymizer SHALL buffer until `[EMAIL_1]` is complete, deanonymize to the original value, and emit `"Contact john@example.com for help"`

#### Scenario: Stream ends with partial placeholder
- **WHEN** the stream ends with a buffered partial placeholder (e.g., `"[EMA"`)
- **THEN** the partial buffer SHALL be flushed as-is (cannot deanonymize incomplete placeholder)

#### Scenario: Stream ends with complete placeholder
- **WHEN** the stream ends with a fully buffered placeholder (e.g., `"[EMAIL_1]"`)
- **THEN** it SHALL be deanonymized and the original value emitted

#### Scenario: Multiple PII placeholders in stream
- **WHEN** a stream contains `"[EMAIL_1] and [PHONE_1]"`
- **THEN** each complete placeholder SHALL be deanonymized individually as it becomes complete

### Requirement: StreamDeanonymizer flush on error
The `StreamDeanonymizer` SHALL flush any buffered content when the stream terminates due to error.

#### Scenario: Error during streaming
- **WHEN** an exception occurs during streaming with buffered content
- **THEN** the buffer SHALL be flushed as-is, and the error SHALL propagate
