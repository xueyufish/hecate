# Capability: output-findings-wiring

## Purpose

Close the long-standing wiring gap where `OutputSecurityHook` accepts a `security_finding_writer` callable in its constructor but neither `create_security_hooks` nor `assemble_guardrails` pass one. As a result, today every DLP finding detected on the LLM output side is silently discarded (BLOCK / MASK still works because those code paths return GuardrailResult directly, but no `SecurityFindingModel` row is ever persisted for the output side, and no SIEM event is ever emitted for output-side DLP).

This capability is the **shared substrate** that both `injection-detection` and `prompt-leakage-protection` depend on. It is scoped to output-side findings only (input-side DLP findings follow a different wiring path through `InputSecurityHook` and are out of scope for this change).

## Requirements

### Requirement: `security_finding_writer` is threaded from assembly to hook

The system SHALL thread the `security_finding_writer` callable through the existing factory + assembly + hook pipeline so that any output-side detection (DLP, injection, prompt leakage) writes to `SecurityFindingModel` when an event store and session id are available.

The wiring path is:

```
assemble_guardrails (services/security/guardrail_assembly.py)
  └─ finding_writer: SecurityFindingWriter instance (when event_store + session_id available)
      └─ passes to create_security_hooks(..., finding_writer=writer)
          └─ create_security_hooks (services/security/hooks/__init__.py)
              └─ OutputSecurityHook(..., security_finding_writer=writer)
```

The hook's existing constructor signature (`security_finding_writer: Any = None`) is preserved. The new behavior is that `create_security_hooks` accepts an optional `finding_writer` kwarg and passes it through; `assemble_guardrails` accepts an optional `finding_writer` parameter and constructs the writer from the existing session_id + workspace_id context (no new constructor parameters in production code beyond what's needed to make the writer; it is a pure seam).

#### Scenario: assemble_guardrails constructs the writer when session context is available

- **GIVEN** `event_store` and `session_id` are both available at `assemble_guardrails` call time
- **WHEN** `assemble_guardrails` builds the GuardrailBundle
- **THEN** it SHALL construct a `SecurityFindingWriter` instance bound to the current db session, workspace_id, and session_id
- **AND** it SHALL pass the writer to `create_security_hooks(..., finding_writer=writer)`
- **AND** the writer SHALL be the same instance used by the `DLP scanner` writer pathway (`dlp_scanner`).

#### Scenario: assemble_guardrails omits the writer when session context is missing

- **GIVEN** either `event_store` or `session_id` is None at call time
- **WHEN** `assemble_guardrails` builds the GuardrailBundle
- **THEN** it SHALL NOT construct a writer (None is propagated)
- **AND** the OutputSecurityHook SHALL skip finding writes entirely (existing behavior, no regression).

### Requirement: `SecurityFindingWriter` is the canonical write helper

The system SHALL introduce a `SecurityFindingWriter` class in `services/security/finding_writer.py` that encapsulates the SecurityFindingModel insert + (optional) EventStore event emission. The writer is the single chokepoint for output-side finding persistence, replacing today's direct `security_finding_writer(...)` callable contract with a structured object.

The writer SHALL expose:

- `async def write(entity_type, value, start, end, score, recognizer, action, *, severity="high", rule_name=None, source="output", context=None) -> SecurityFindingModel`
- `async def write_many(findings: Iterable[FindingTuple]) -> int` (batch write optimization)

The writer SHALL construct the `SecurityFindingModel` row with:

- `org_id` and `workspace_id` from the constructor-bound context
- `rule_name` derived from `(source, recognizer)` — e.g., `output.dlp.email`, `output.injection_detection.code_python`, `output.prompt_leakage.rules`
- `severity` from the call argument (default `high`)
- `message` synthesized from `entity_type` + matched `value` (truncated to 256 chars to prevent log spam from long matched values)
- `metadata_` = `{"source": source, "recognizer": recognizer, "context": context or {}, "span": [start, end]}`
- `user_id` and `session_id` propagated from the writer context (when available)

#### Scenario: writer produces a persisted SecurityFindingModel

- **GIVEN** a writer bound to (db, org_id, workspace_id, session_id, user_id)
- **WHEN** `write(entity_type="CODE_PYTHON_INJECTION", value="eval(", recognizer="code_python", action="audit")` is called
- **THEN** a SecurityFindingModel row SHALL be persisted
- **AND** the row SHALL have `rule_name="output.injection_detection.code_python"`, `severity="high"`, and `metadata_["source"]=="output"`.

#### Scenario: writer skips when db session is missing

- **GIVEN** the writer is constructed without a db session
- **WHEN** `write(...)` is called
- **THEN** the writer SHALL log a warning (once per turn, deduplicated) and return None
- **AND** it SHALL NOT raise (graceful degradation).

### Requirement: DLP findings now flow through the writer too

As a side effect of the wiring fix, today-discarded DLP output-side findings SHALL now be persisted. The DLP scanner's `result.findings` list SHALL be iterated by `OutputSecurityHook._write_audit_records` (existing method) and each finding SHALL be sent to the writer.

This is a deliberate expansion of the capability. The DLP module itself is unchanged — the writer contract is the same one already used by `OutputSecurityHook`.

#### Scenario: DLP blocks output with EMAIL PII

- **GIVEN** DLP scanner returns BLOCK action with finding `entity_type="EMAIL_ADDRESS", value="user@example.com"`
- **WHEN** the hook calls `_write_audit_records(result, response)`
- **AND** the writer is available
- **THEN** a SecurityFindingModel row SHALL be persisted with `rule_name="output.dlp.email_address"` and `metadata_["action"]="block"`
- **AND** the existing BLOCK behavior is preserved (no behavior change for the user-facing flow).

#### Scenario: DLP masks output with SSN PII

- **GIVEN** DLP scanner returns MASK action with finding `entity_type="US_SSN", value="123-45-6789"`
- **WHEN** the hook calls `_write_audit_records(result, response)`
- **THEN** a SecurityFindingModel row SHALL be persisted with `rule_name="output.dlp.us_ssn"` and `metadata_["action"]="mask"`
- **AND** the mask substitution (e.g., `XXX-XX-XXXX`) SHALL still be applied to the response.

### Requirement: Backward compatibility with direct callable contract

The system SHALL preserve the existing `security_finding_writer: Any = None` parameter on `OutputSecurityHook.__init__` for backward compatibility with any external code that constructs the hook directly (tests, third-party integrators).

If the parameter is a callable (not a `SecurityFindingWriter` instance), the hook SHALL wrap it in an adapter that delegates to the callable's signature. If the parameter is a `SecurityFindingWriter` instance, the hook SHALL use it directly. If neither, the hook SHALL skip writing (existing behavior).

#### Scenario: existing test passes a callable writer

- **GIVEN** a test constructs `OutputSecurityHook(..., security_finding_writer=lambda **kwargs: None)`
- **WHEN** the hook is invoked
- **THEN** the callable SHALL be invoked with the existing kwargs (backward compat)
- **AND** the test SHALL continue to pass without modification.

### Requirement: Configuration surface is `output_findings` section

The system SHALL expose `guardrail_config["output_findings"]` as an optional section with the following shape:

```python
{
  "enabled": True,                # default True; False disables the entire wiring
  "persist_to_db": True,          # default True; if False, only emit EventStore events, no DB rows
  "emit_event": True,             # default True; if False, only persist DB rows, no event emission
  "siem_severity_floor": "medium" # default "medium"; findings below this severity are NOT exported via SIEM
}
```

When `output_findings["enabled"]` is False, the entire writer is None and no findings are persisted (no behavior change from today — the historical bug state). When True, all other settings apply.

`siem_severity_floor` is enforced by the SIEM collector (`services/security/siem/collector.py`) which already filters by severity — this capability just sets the floor for output-side findings.

### Requirement: Writer integrates into existing SIEM pipeline (no SIEM changes)

The system SHALL NOT modify the SIEM collector, exporter, or any formatter. The writer persists `SecurityFindingModel` rows; the SIEM collector picks them up via its existing `from_security_finding` pathway. This is the same mechanism that already exists for audit-pipeline findings (`services/audit/writer.py:188`); the writer simply adds another write site for output-side findings.

#### Scenario: SIEM exports new output-side findings

- **GIVEN** the writer persists 5 output-side findings (mix of DLP, injection detection, prompt leakage)
- **WHEN** the SIEM collector runs its periodic export
- **THEN** all 5 findings SHALL be normalized to OCSF `Security Finding` events
- **AND** SHALL be exported via the configured Webhook / Syslog channel
- **AND** no SIEM code SHALL be modified.

### Requirement: Deduplication handles overlapping DLP + prompt_leakage findings

When the prompt leakage detector fires CRITICAL severity (secrets reproduction) and the DLP scanner also fires on the same `api[_-]?key` substring (overlap is expected), the system SHALL emit two findings but mark one as `metadata_["deduplicated_with"]` pointing to the other's id. The SIEM collector SHALL dedupe these in its normalization step.

This prevents double-counting in compliance dashboards while preserving both signals in the audit log.

#### Scenario: API key reproduced in LLM response

- **GIVEN** system prompt contains `API_KEY=XK9F-EXAMPLE-12345` and the LLM response contains the same substring
- **WHEN** both prompt_leakage (CRITICAL, secrets category) and DLP (secrets recognizer) fire
- **THEN** two findings SHALL be persisted
- **AND** the later one SHALL have `metadata_["deduplicated_with"]` pointing to the first
- **AND** the SIEM collector SHALL emit a single OCSF event with both sources in `finding_info`.

### Requirement: writer is bound per-turn, not global

The `SecurityFindingWriter` instance SHALL be created at `assemble_guardrails` call time and SHALL NOT be cached across turns or across agents. This prevents accidental cross-workspace / cross-user findings leakage in shared worker pools.

The writer's lifetime SHALL be bounded by the `GuardrailBundle` returned from `assemble_guardrails` (which itself is bounded by the per-execution-path lifetime — see `guardrail-upgrade-trio` T1 for the bundle's lifetime semantics).

### Requirement: errors during writer invocation are caught and logged

If `SecurityFindingWriter.write(...)` raises an exception (DB connection drop, EventStore timeout, etc.), the hook SHALL:

1. Log the exception at WARNING level with `session_id`, `agent_id`, and the recognizer id.
2. Continue execution — the finding is best-effort and SHALL NOT cause the LLM call to fail.
3. Emit an `EventType.ERROR` event with `payload={"source": "output_findings_writer", "error": "<class name>", "recognizer": "<id>"}`.

This matches the existing fail-safe pattern in `prompt-leakage-protection` capability (fingerprint compute failure).

### Requirement: backward-compat test for direct hook construction without writer

A test SHALL exist that constructs `OutputSecurityHook` directly with no `security_finding_writer` (the historical construction), invokes it on a response with DLP findings, and asserts that the hook still returns the correct `GuardrailResult` (BLOCK / MASK / ALLOW per DLP) without raising. This test guards against accidental regression when wiring is changed.