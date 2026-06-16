## 1. Engine Foundation — GuardrailAction.SANITIZE

- [x] 1.1 Add `SANITIZE = "sanitize"` to `GuardrailAction` enum in `src/hecate/engine/guardrail.py`
- [x] 1.2 Add `modified_data: dict | None = None` field to `GuardrailResult` dataclass
- [x] 1.3 Update module docstring to remove "Deferred to P3: modify action" note
- [x] 1.4 Write tests: `GuardrailAction` has 3 members, SANITIZE string value, `GuardrailResult` with modified_data

## 2. Worker SANITIZE Handling

- [x] 2.1 Update `LLMWorker.execute()` to handle SANITIZE from PreLLMHook: use `modified_data["messages"]` for LLM call
- [x] 2.2 Update `LLMWorker.execute()` to handle SANITIZE from PostLLMHook: use `modified_data["response"]` in channel updates
- [x] 2.3 Update `LLMWorker.execute_stream()` to handle SANITIZE from PreLLMHook (same as execute)
- [x] 2.4 Update `LLMWorker.execute_stream()` to handle SANITIZE from PostLLMHook in streaming path
- [x] 2.5 Update `ToolWorker._execute_single_tool()` to handle SANITIZE from PostToolHook: use `modified_data["result"]`
- [x] 2.6 Add warning log when SANITIZE returned with `modified_data=None` (treat as ALLOW)
- [x] 2.7 Write tests: LLMWorker SANITIZE from pre-hook, SANITIZE from post-hook, ToolWorker SANITIZE, SANITIZE with None data

## 3. AgentModel guardrail_config Column

- [x] 3.1 Add `guardrail_config` JSONB nullable column to `AgentModel` in `src/hecate/models/agent.py`
- [x] 3.2 Add `guardrail_config` field to `AgentCreateSchema` with default None
- [x] 3.3 Add `guardrail_config` field to `AgentUpdateSchema` with default None
- [x] 3.4 Add `guardrail_config` field to `AgentReadSchema` with default None
- [x] 3.5 Create Alembic migration: add `guardrail_config` JSONB column to `agents` table (nullable, default NULL)
- [x] 3.6 Write tests: AgentModel CRUD with guardrail_config, default None, JSON round-trip

## 4. Delete NeMo Guardrails Stub

- [x] 4.1 Delete `src/hecate/services/security/nemo_guardrails.py`
- [x] 4.2 Remove `nemo_config` import from `src/hecate/services/security/middleware.py`
- [x] 4.3 Refactor `SecurityMiddleware.check_input()` to remove NeMo call, use LLMGuardScanner only
- [x] 4.4 Remove `nemoguardrails` from `pyproject.toml` `[security]` extras (will re-add in 9.1a)
- [x] 4.5 Update `services/security/__init__.py` if it exports `nemo_config`
- [x] 4.6 Write tests: SecurityMiddleware without NeMo, middleware check_input/output still works

## 5. InputSecurityHook (Feature 9.1)

- [x] 5.1 Create `src/hecate/services/security/hooks/` package with `__init__.py`
- [x] 5.2 Implement `InputSecurityHook` class (PreLLMHook) in `hooks/input_security.py`
- [x] 5.3 Implement PII anonymization in messages using `PIIAnonymizer` with configurable entity types
- [x] 5.4 Implement prompt injection detection using `LLMGuardScanner`
- [x] 5.5 Implement secrets detection using `LLMGuardScanner`
- [x] 5.6 Implement configurable behavior: `block_on_injection` flag (True=BLOCK, False=SANITIZE with warning)
- [x] 5.7 Store PII mappings in execution context `_pii_mappings` for downstream deanonymization
- [x] 5.8 Handle `enabled=False` and `guardrail_config=None` (return ALLOW immediately)
- [x] 5.9 Write tests: clean messages, PII detection, injection detection, secrets detection, disabled config, entity type filtering

## 6. OutputSecurityHook (Feature 9.2)

- [x] 6.1 Implement `OutputSecurityHook` class (PostLLMHook) in `hooks/output_security.py`
- [x] 6.2 Implement output toxicity detection using `LLMGuardScanner`
- [x] 6.3 Implement PII deanonymization in non-streaming response using session `_pii_mappings`
- [x] 6.4 Handle `deanonymize=False` config (pass placeholders through)
- [x] 6.5 Handle `enabled=False` and `guardrail_config=None` (return ALLOW immediately)
- [x] 6.6 Write tests: clean response, toxicity detection, deanonymization, disabled config, missing mappings

## 7. StreamDeanonymizer (Feature 9.2 Streaming)

- [x] 7.1 Implement `StreamDeanonymizer` class in `hooks/stream_deanonymizer.py`
- [x] 7.2 Implement token buffering: detect `[` start, accumulate until `]` end
- [x] 7.3 Implement placeholder lookup and deanonymization for complete placeholders
- [x] 7.4 Implement immediate pass-through for non-PII tokens
- [x] 7.5 Implement `flush()` method for stream end: deanonymize complete placeholders, emit partial as-is
- [x] 7.6 Implement error handling: flush buffer on exception, propagate error
- [x] 7.7 Write tests: non-PII tokens, split placeholder, multiple placeholders, flush complete, flush partial, error during streaming

## 8. ToolResultSecurityHook (Feature 9.5)

- [x] 8.1 Implement `ToolResultSecurityHook` class (PostToolHook) in `hooks/tool_result_security.py`
- [x] 8.2 Implement PII detection and masking in tool result strings using `PIIAnonymizer`
- [x] 8.3 Handle `mask_tool_results=False` config (pass through)
- [x] 8.4 Handle `data_security` not configured (return ALLOW)
- [x] 8.5 Write tests: clean result, PII in result, masking disabled, security disabled

## 9. Data Security — Storage and Encryption (Feature 9.5)

- [x] 9.1 Create `PIIMappingModel` ORM model in `src/hecate/models/pii_mapping.py` with fields: id, session_id, placeholder, encrypted_value, pii_type, created_at
- [x] 9.2 Add unique constraint on (session_id, placeholder)
- [x] 9.3 Add `PIIMappingModel` to `Base.metadata` via import in models `__init__.py`
- [x] 9.4 Create Alembic migration: create `pii_mappings` table
- [x] 9.5 Implement Fernet encryption/decryption helper in `services/security/encryption.py`
- [x] 9.6 Implement `mask_and_encrypt` mode: encrypt original PII, store in `PIIMappingModel`
- [x] 9.7 Implement `ConfigurationError` when `mask_and_encrypt` requested without `FERNET_KEY`
- [x] 9.8 Implement `mask_only` mode: replace PII with irreversible placeholders, no storage
- [x] 9.9 Write tests: PIIMappingModel CRUD, Fernet encrypt/decrypt, both storage modes, missing Fernet key

## 10. Security Hook Factory

- [x] 10.1 Implement `create_security_hooks(guardrail_config: dict | None) -> SecurityHookSet` factory function
- [x] 10.2 Define `SecurityHookSet` named tuple: `(pre_llm_hook, post_llm_hook, pre_tool_hook, post_tool_hook)`
- [x] 10.3 Factory returns NoOp hooks when config is None or all sections disabled
- [x] 10.4 Factory constructs InputSecurityHook with `input_security` config section
- [x] 10.5 Factory constructs OutputSecurityHook with `output_security` config section
- [x] 10.6 Factory constructs ToolResultSecurityHook with `data_security` config section
- [x] 10.7 Export `create_security_hooks` and `SecurityHookSet` from `hooks/__init__.py`
- [x] 10.8 Write tests: factory with None config, factory with disabled sections, factory with full config

## 11. LLMGuardScanner Enhancement

- [x] 11.1 Add `sanitized_text: str | None = None` field to `ScanResult` dataclass
- [x] 11.2 Update `scan_prompt()` to capture and return sanitized text from Anonymize scanner
- [x] 11.3 Update `scan_output()` to capture and return sanitized text from output scanners
- [x] 11.4 Update mock scanner to return sanitized_text
- [x] 11.5 Write tests: ScanResult with sanitized_text, scan_prompt returns anonymized text

## 12. PII Audit Events

- [x] 12.1 Add `PII_DETECTED` event type to `EventType` enum in `engine/eventstore.py`
- [x] 12.2 Implement audit logging in InputSecurityHook when `audit_pii_events` is True
- [x] 12.3 Implement audit logging in OutputSecurityHook when `audit_pii_events` is True
- [x] 12.4 Implement audit logging in ToolResultSecurityHook when `audit_pii_events` is True
- [x] 12.5 Ensure audit events contain pii_type and placeholder count but NOT original PII values
- [x] 12.6 Write tests: audit events emitted, audit events do not contain original PII, audit disabled

## 13. Feature Catalog & Roadmap Update

- [x] 13.1 Update `docs/features/feature-catalog.md`: mark features 9.1, 9.2 with ✅ after verification
- [x] 13.2 Update `docs/features/feature-catalog.md`: move `GuardrailAction.modify` (SANITIZE) from P3 to P2 status
- [x] 13.3 Update `docs/features/roadmap.md`: mark security milestone items as complete
- [x] 13.4 Update statistics counts in feature catalog
