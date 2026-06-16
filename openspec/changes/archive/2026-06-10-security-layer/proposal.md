## Why

The security infrastructure in `services/security/` is fully built (LLMGuardScanner for prompt injection/PII/secrets/toxicity, PIIAnonymizer for reversible masking, SecurityMiddleware for orchestration) but remains completely dormant — no component imports SecurityMiddleware, LLMWorker and ToolWorker default to NoOp hooks, and GuardrailAction only supports ALLOW/BLOCK with no mechanism to transform data in-flight. As a result, the platform has zero runtime security: no prompt injection detection, no PII redaction, no output toxicity filtering, and no tool-result sanitization. Features 9.1 (Input Security), 9.2 (Output Security), and 9.5 (Data Security) all depend on wiring this existing infrastructure into the engine's guardrail hook lifecycle with per-agent configurability.

## What Changes

- Add `GuardrailAction.SANITIZE` and `GuardrailResult.modified_data: dict | None` to the engine guardrail ABC, enabling hooks to transform data (PII masking, content rewriting) rather than only allow/block.
- Update LLMWorker and ToolWorker to handle the SANITIZE action: replace messages/response/tool-result with `modified_data` when returned.
- Add `guardrail_config` JSONB column to `AgentModel` with Alembic migration, enabling per-agent security configuration (which scanners to enable, PII entity types, storage mode, etc.).
- Implement `InputSecurityHook` (PreLLMHook): prompt injection detection via LLMGuardScanner, PII anonymization via PIIAnonymizer, returning SANITIZE with masked messages when PII is detected, or BLOCK when injection is detected.
- Implement `OutputSecurityHook` (PostLLMHook): output toxicity detection via LLMGuardScanner, PII deanonymization for non-streaming responses, returning SANITIZE with sanitized response when issues are found.
- Implement `StreamDeanonymizer`: buffer-based token accumulator for streaming LLM responses that collects partial tokens, deanonymizes complete PII placeholders, and emits only fully restored text — ensuring streaming never exposes raw PII placeholders to end users.
- Implement `ToolResultSecurityHook` (PostToolHook): PII detection and masking in tool execution results, returning SANITIZE with masked result when PII is found.
- Add configurable PII storage mode per-agent: `mask_only` (default, irreversible masking before storage) or `mask_and_encrypt` (Fernet-encrypted PIIMappingModel for reversible lookups).
- Delete `nemo_guardrails.py` stub (regex-only toy, no real NeMo runtime; feature 9.1a will redesign from scratch).
- Refactor `SecurityMiddleware` to remove NeMo dependency, serving as a thin facade over the new hooks for backward compatibility.

## Capabilities

### New Capabilities
- `input-security`: Input security hook implementing PreLLMHook — prompt injection detection, PII anonymization, harmful content filtering for feature 9.1
- `output-security`: Output security hook implementing PostLLMHook — toxicity detection, PII deanonymization (non-streaming + StreamDeanonymizer for streaming), sensitive output blocking for feature 9.2
- `data-security`: At-rest data security — PII masking before database storage, configurable storage mode (mask_only/mask_and_encrypt), Fernet-encrypted PIIMappingModel for reversible lookups, PostToolHook for tool result sanitization, audit event logging for feature 9.5

### Modified Capabilities
- `guardrail-hook`: Add `GuardrailAction.SANITIZE` enum member, add `modified_data: dict | None` to `GuardrailResult`, update NoOp implementations to support new fields, update spec scenarios for three-member enum and new dataclass field
- `security-mcp`: Remove NeMo Guardrails stub integration from SecurityMiddleware, update scanner orchestration to use new hook-based architecture, add PIIAnonymizer integration with LLMGuardScanner Anonymize scanner for coordinated detection

## Impact

- **Engine layer** (`engine/guardrail.py`): GuardrailAction enum gains SANITIZE member; GuardrailResult dataclass gains modified_data field — all existing NoOp implementations remain compatible
- **Workers** (`engine/workers/llm_worker.py`, `engine/workers/tool_worker.py`): Both gain SANITIZE action handling in execute() and execute_stream() paths; LLMWorker streaming path adds StreamDeanonymizer integration
- **Models** (`models/agent.py`): AgentModel gains `guardrail_config` JSONB column; AgentCreateSchema, AgentUpdateSchema, AgentReadSchema gain corresponding field with Pydantic alias pattern
- **Database**: New Alembic migration adding `guardrail_config` column to `agents` table
- **Services** (`services/security/`): Delete `nemo_guardrails.py`; refactor `middleware.py` to remove NeMo import; add `hooks/` submodule containing InputSecurityHook, OutputSecurityHook, ToolResultSecurityHook, StreamDeanonymizer
- **Config** (`core/config.py`): `FERNET_KEY` setting (already declared) will be actively used for mask_and_encrypt mode
- **Tests**: New test files for each hook implementation, StreamDeanonymizer, and updated guardrail ABC tests; existing guardrail-hook and security-mcp tests need updates for SANITIZE action
- **Dependencies**: No new dependencies — uses existing llm-guard, cryptography (Fernet), and regex libraries
