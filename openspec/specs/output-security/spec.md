# output-security Specification

## Purpose

Define the OutputSecurityHook PostLLMHook providing output toxicity detection and PII deanonymization for LLM responses.
## Requirements
### Requirement: OutputSecurityHook implements PostLLMHook
The `OutputSecurityHook` SHALL implement the `PostLLMHook` ABC, providing output toxicity detection, PII deanonymization, and post-deanonymization DLP egress scanning for LLM responses.

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

### Requirement: OutputSecurityHook applies DLP after deanonymization
The `OutputSecurityHook` SHALL call `DLPScanner.scan(direction="llm_output")` on the deanonymized response text, after the deanonymization step but before returning to the user.

#### Scenario: DLP enabled and deanonymized text clean
- **WHEN** `DLP_ENABLED=True` and DLPScanner is injected, and the deanonymized response contains no sensitive entities
- **THEN** the hook SHALL return `GuardrailResult(action=ALLOW, modified_data={"response": <deanonymized_response>})`

#### Scenario: DLP BLOCK on deanonymized response
- **WHEN** the deanonymized response contains secrets (e.g., AWS_ACCESS_KEY) and policy says BLOCK
- **THEN** the hook SHALL return `GuardrailResult(action=BLOCK, reason="DLP blocked sensitive entity in llm_output: AWS_ACCESS_KEY")` — not returning the deanonymized response

#### Scenario: DLP MASK on deanonymized response
- **WHEN** the deanonymized response contains EMAIL and policy says MASK
- **THEN** the hook SHALL return `GuardrailResult(action=SANITIZE, modified_data={"response": <text with EMAIL masked to [EMAIL]>})`

#### Scenario: DLP AUDIT on deanonymized response
- **WHEN** the deanonymized response contains EMAIL and policy says AUDIT
- **THEN** the hook SHALL return the deanonymized response unchanged but record a SecurityFinding with `rule_name="dlp:email_audit"`

#### Scenario: DLP disabled
- **WHEN** `DLP_ENABLED=False` or DLPScanner is None
- **THEN** the hook SHALL skip DLP scanning (backward compatibility)

### Requirement: Deanonymization is trust boundary 1, DLP is trust boundary 2
The `OutputSecurityHook` SHALL preserve deanonymization (trust boundary 1, reversibly restoring PII for the user) as a separate concern from DLP scanning (trust boundary 2, enforcing egress policy on real values). Deanonymization runs first, then DLP scans the restored values.

#### Scenario: Two-stage processing
- **WHEN** the LLM output contains `[EMAIL_1]` and the original was `john@example.com`
- **THEN** Stage 1 (deanonymize) replaces `[EMAIL_1]` → `john@example.com`
- **AND** Stage 2 (DLP scan) scans the text with `john@example.com` (real value) and applies policy

