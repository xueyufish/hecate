## Context

Hecate has a fully-built security service layer in `services/security/` consisting of three components:

1. **LLMGuardScanner** (`llm_guard.py`) — lazy-loads llm-guard scanners with mock fallback. Prompt scanners: PromptInjection (threshold=0.5), Anonymize, Secrets. Output scanners: Toxicity (threshold=0.7). Returns `ScanResult(is_safe, score, issues)`.

2. **PIIAnonymizer** (`anonymizer.py`) — regex-based reversible PII masking for email, phone, credit_card, ssn, ip_address. Returns `AnonymizedText(text, mappings)` with placeholder pattern `[TYPE_N]`.

3. **SecurityMiddleware** (`middleware.py`) — orchestrates LLMGuardScanner + NeMo Guardrails. Currently the only integration point, but **nobody imports it**.

The engine layer has four guardrail hook ABCs (PreLLMHook, PostLLMHook, PreToolHook, PostToolHook) in `engine/guardrail.py` with NoOp defaults. LLMWorker and ToolWorker accept hooks at construction time and handle BLOCK correctly, but default to NoOp.

**The gap**: No bridge exists between the service-layer security scanners and the engine-layer hooks. GuardrailAction only has ALLOW/BLOCK — no mechanism for in-flight data transformation (PII masking). AgentModel has no per-agent security configuration. The NeMo Guardrails stub is regex-only toy code.

## Goals / Non-Goals

**Goals:**
- Wire existing security scanners into engine guardrail hooks so every LLM call, tool execution, and streaming response passes through security checks
- Enable per-agent security configuration via `guardrail_config` JSONB column
- Support SANITIZE action for in-flight PII masking with configurable storage modes
- Handle streaming PII safely with buffer-based deanonymization (no raw placeholders leaked to users)
- Cover all four data flow layers: user input, LLM output, tool results, database storage

**Non-Goals:**
- NeMo Guardrails integration — delete stub, defer to 9.1a
- Prompt injection model fine-tuning — use LLM Guard's DeBERTa-v3 as-is
- Whole-field content encryption — destroys search capability; use PII-level masking instead
- Content moderation (keyword filtering) — deferred to 9.2a (P3)
- Hallucination detection — deferred to future work
- Multi-tenant security isolation — already completed in feature 10.5

## Decisions

### D1: Add SANITIZE action to GuardrailAction enum

**Decision**: Extend `GuardrailAction` from `{ALLOW, BLOCK}` to `{ALLOW, BLOCK, SANITIZE}`. Add `modified_data: dict | None = None` to `GuardrailResult`.

**Rationale**: PII masking requires the hook to return transformed data (anonymized messages, masked response). ALLOW means "pass through unchanged"; BLOCK means "halt execution"; SANITIZE means "continue with modified data." This is the standard three-action pattern used by AWS Bedrock Guardrails and Google Cloud DLP.

**Alternative considered**: Return ALLOW with modified data attached — conflates two concerns, makes it unclear whether data was transformed.

### D2: Per-agent guardrail configuration via JSONB column

**Decision**: Add `guardrail_config` JSONB column to `AgentModel`. Structure:

```python
{
    "input_security": {
        "enabled": True,
        "prompt_injection_threshold": 0.5,
        "pii_entities": ["email", "phone", "ssn", "credit_card", "ip_address"],
        "block_on_injection": True
    },
    "output_security": {
        "enabled": True,
        "toxicity_threshold": 0.7,
        "deanonymize": True
    },
    "data_security": {
        "pii_storage_mode": "mask_only",  # or "mask_and_encrypt"
        "mask_tool_results": True,
        "audit_pii_events": True
    }
}
```

**Rationale**: Different agents handle different sensitivity levels. A customer-support agent needs aggressive PII masking; an internal dev tool may only need prompt injection detection. JSONB avoids schema migrations for config changes.

**Alternative considered**: Global config only — rejected because enterprise deployments need per-agent control. Separate security profile table — over-engineering for P2 scope.

### D3: Streaming PII via buffer-based StreamDeanonymizer

**Decision**: Implement `StreamDeanonymizer` that buffers incoming tokens, waits for complete PII placeholders (`[EMAIL_1]`), then deanonymizes and emits. Partial buffers are held until more tokens arrive or stream ends.

```
Token stream: "Contact [", "EMAIL_", "1]", " for details"
              ↓ buffer  ↓ buffer ↓ flush  ↓ pass-through
Emitted:      ""        ""       "john@x.com" " for details"
```

**Rationale**: LLM tokens may split PII placeholders across multiple chunks. Emitting raw `[EMAIL_1]` to the user is a PII leak (reveals masking occurred and placeholder format). Buffer-based approach is used by Upsonic's StreamDeanonymizer and Salesforce Einstein Trust Layer.

**Alternative considered**: Post-stream deanonymization (buffer entire response) — defeats the purpose of streaming. Regex replacement per token — unreliable for split placeholders.

### D4: Configurable PII storage mode

**Decision**: Two modes controlled by `guardrail_config.data_security.pii_storage_mode`:

- **`mask_only`** (default): PII is replaced with irreversible placeholders before storage. No recovery possible. Matches Salesforce Einstein Trust Layer pattern.
- **`mask_and_encrypt`**: Original PII values are Fernet-encrypted and stored in a separate `PIIMappingModel` table keyed by (session_id, placeholder). Allows authorized recovery for compliance/audit. Matches Google DLP reversible tokenization pattern.

**Rationale**: Enterprise customers have different compliance requirements. Some need irreversible masking (GDPR right-to-erasure simplification); others need the ability to recover original values for authorized use cases (customer support escalation).

**Alternative considered**: Only `mask_only` — too restrictive for enterprise. Only `mask_and_encrypt` — unnecessary complexity and key management burden for most deployments.

### D5: Delete NeMo Guardrails stub

**Decision**: Delete `services/security/nemo_guardrails.py` entirely. Remove its import from `middleware.py`. Feature 9.1a will design real NeMo integration from scratch.

**Rationale**: Current `NeMoGuardrailsConfig` is a regex-only stub with hardcoded patterns ("hack", "exploit", "bomb"). It provides zero real security and creates a false sense of protection. The real NeMo Guardrails library requires a Colang runtime, config files, and an async server — completely different architecture.

### D6: Hook wiring via factory function

**Decision**: Create a `create_security_hooks(guardrail_config: dict) -> SecurityHookSet` factory function that returns a named tuple of `(pre_llm_hook, post_llm_hook, pre_tool_hook, post_tool_hook)` configured according to the agent's guardrail_config. Workers receive these hooks at construction time (existing pattern).

**Rationale**: Workers already accept hooks via constructor injection. The factory centralizes the "read config → create hooks" logic in one place, keeping workers unaware of security implementation details.

**Alternative considered**: Workers directly instantiate hooks — violates separation of concerns, creates import cycle between engine/ and services/security/.

### D7: Fernet for mask_and_encrypt mode only

**Decision**: Use `cryptography.fernet.Fernet` (already in optional deps via `FERNET_KEY` config) exclusively for encrypting PII mappings in `mask_and_encrypt` mode. No encryption of full content fields.

**Rationale**: Full-field encryption destroys search/query capability (industry consensus from Google DLP, AWS Macie, Salesforce). Fernet provides symmetric authenticated encryption suitable for reversible tokenization. Key is already declared in `core/config.py`.

## Risks / Trade-offs

**[Streaming latency]** Buffer-based deanonymizer adds latency proportional to placeholder length (~10-20 chars). → Mitigation: emit non-PII text immediately; only buffer when `[` is detected.

**[PII mapping loss on crash]** In-memory `AnonymizedText.mappings` are lost on process crash, breaking deanonymization for in-progress streams. → Mitigation: `mask_and_encrypt` mode persists mappings to DB; `mask_only` mode has no mappings to lose. For streaming, the StreamDeanonymizer flushes on stream end or error.

**[Regex PII false positives/negatives]** PIIAnonymizer uses regex patterns which miss context-dependent PII (names, addresses) and may false-positive on formatted numbers. → Mitigation: LLM Guard's Anonymize scanner uses Presidio + BERT NER for more accurate detection. Layer both: PIIAnonymizer for fast regex, LLM Guard Anonymize for NER.

**[Migration risk]** Adding `guardrail_config` column requires Alembic migration. Existing agents get `NULL` → treated as "security disabled" (backward compatible). → Mitigation: `None` guardrail_config means no hooks are created (factory returns NoOp set).

**[Performance]** Running 3 input scanners (PromptInjection, Anonymize, Secrets) on every LLM call adds latency. → Mitigation: scanners are async-compatible; Anonymize scanner is optional via config; lazy-loading means no overhead if disabled.

**[Fernet key management]** `mask_and_encrypt` mode requires secure Fernet key storage. Key rotation is not yet supported. → Mitigation: Document key management requirements; defer key rotation to P3.
