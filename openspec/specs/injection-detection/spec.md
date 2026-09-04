# Capability: injection-detection

## Purpose

Detect injection patterns (code / SQL / template / XSS) in LLM-generated output before the output flows to downstream systems (code interpreter, database query, template renderer, HTML page), and emit typed security findings for downstream consumers (audit log, SIEM, REST API).

## Requirements

### Requirement: Built-in recognizers cover four canonical injection types

The system SHALL provide four built-in recognizers under `services/security/output/injection_detection/recognizers/`:

| Recognizer id | Target pattern family | Default severity | Reference |
|-----|------|------|------|
| `code_python` | Python code injection (`eval`, `exec`, `__import__`, `compile`, `subprocess`, `os.system` with string concat) | HIGH | LLM01:2025 Scenario #5 (CVE-2024-5184 email-assistant code injection); Bedrock Guardrails Standard tier "code elements" |
| `sql_injection` | DDL/DML hostile statements (`DROP TABLE`, `UNION SELECT`, `;DELETE`, `--` comment escape, `' OR '1'='1`) | HIGH | LLM01:2025 Scenario #2 (indirect injection via DB tool); Bedrock Guardrails Standard tier "string literals" |
| `template_jinja` | Jinja SSTI (`{{ config }}`, `{% import %}`, `{% include %}`, `{{ self.__class__ }}`) | HIGH | DeerFlow SkillScan "shell execution" category; Bedrock Standard tier "code elements" |
| `xss` | HTML/JS execution (`<script>`, `onerror=`, `javascript:`, `<iframe>`, `<svg onload=`) | HIGH | DeerFlow "Active HTML/XSS artifact forced download at Gateway"; OWASP LLM01 multimodal injection

Each recognizer SHALL contribute at minimum 3 deterministic regex patterns derived from the reference vectors above. Patterns SHALL be compiled at module import time and exposed as a public `PATTERNS: tuple[re.Pattern[str], ...]` on the recognizer class for testability.

#### Scenario: code_python recognizer fires on `eval(input("enter code: "))`

- **WHEN** an LLM response contains the substring `eval(` followed by a non-identifier argument (heuristic: followed by `(` or `input(` or string concat)
- **THEN** the recognizer SHALL emit one finding with `entity_type="CODE_PYTHON_INJECTION"`, `recognizer="code_python"`, `severity="high"`, and the matched span (start, end offsets).
- **AND** the finding SHALL flow through the existing `security_finding_writer` contract (see `output-findings-wiring` capability).

#### Scenario: sql_injection recognizer fires on `DROP TABLE users;--`

- **WHEN** an LLM response contains a regex match in the SQL family patterns
- **THEN** the recognizer SHALL emit one finding with `entity_type="SQL_INJECTION"`, `recognizer="sql_injection"`, `severity="high"`.

#### Scenario: xss recognizer fires on `<img src=x onerror=alert(1)>`

- **WHEN** an LLM response contains a regex match in the XSS family patterns
- **THEN** the recognizer SHALL emit one finding with `entity_type="XSS_INJECTION"`, `recognizer="xss"`, `severity="high"`.

#### Scenario: template_jinja recognizer fires on `{{ config.__class__.__init__.__globals__['os'].popen('id').read() }}`

- **WHEN** an LLM response contains a regex match in the Jinja SSTI family
- **THEN** the recognizer SHALL emit one finding with `entity_type="TEMPLATE_INJECTION"`, `recognizer="template_jinja"`, `severity="high"`.

### Requirement: Per-type action is configurable with safe defaults

The system SHALL expose per-recognizer action configuration under `guardrail_config["injection_detection"]["types"][<recognizer_id>]["action"]`. Allowed values: `audit`, `block`, `mask`, `sanitize`.

The default action for ALL built-in recognizers SHALL be `audit` (findings-only, no output modification). This is a deliberate safe default because PostLLMHook does not have downstream-sink metadata — a coding assistant legitimately outputs SQL and `eval` examples. Per-workspace override is permitted via `ToolPolicyRuleModel`-equivalent surface (see `output-findings-wiring` capability for the broader configuration pattern).

#### Scenario: workspace sets `code_python` action to `block`

- **GIVEN** `guardrail_config["injection_detection"]["types"]["code_python"]["action"] == "block"`
- **WHEN** the recognizer emits a finding for `code_python`
- **THEN** the post-LLM hook SHALL return `GuardrailResult(action=GuardrailAction.BLOCK, reason=...)` with the recognizer id surfaced in the reason text.

#### Scenario: default action does not block normal coding-assistant output

- **GIVEN** no per-type override is set (default `audit` for all built-in recognizers)
- **WHEN** an LLM response contains a SQL example explaining injection patterns to a security-training user
- **THEN** the post-LLM hook SHALL emit findings to the writer
- **AND** SHALL NOT modify the response content (returns `SANITIZE` with no `modified_data` is treated as ALLOW per guardrail-upgrade-trio T1 fix).

### Requirement: Custom patterns are accepted via configuration

The system SHALL accept user-supplied regex patterns via `guardrail_config["injection_detection"]["custom_patterns"]` — a list of `{entity_type, pattern, severity, recognizer}` entries. Patterns SHALL be compiled at `create_security_hooks` invocation time and SHALL be subject to regex timeout (default 50ms) to prevent ReDoS.

#### Scenario: workspace adds a custom MongoDB injection pattern

- **GIVEN** a custom_pattern with `entity_type="MONGO_INJECTION"`, `pattern="\\$where\\s*:\\s*['\\\"]"`, `severity="high"`, `recognizer="custom_1"`
- **WHEN** an LLM response contains `$where: "this.password.match(/.*/)"`
- **THEN** the recognizer SHALL emit one finding with the configured metadata
- **AND** the finding SHALL be auditable in `SecurityFindingModel` with `metadata_["source"] == "injection_detection"` and `metadata_["recognizer"] == "custom_1"`.

### Requirement: Detector emits findings through the standard writer contract

The system SHALL reuse the existing `security_finding_writer` callable contract (`callable(entity_type, value, start, end, score, recognizer, action, context)`). This is the same callable signature used by DLP output scanning today; the wiring detail lives in the `output-findings-wiring` capability.

Each emitted finding SHALL set `context={"source": "injection_detection", "recognizer": <id>, "entity_type": <canonical name>}` to distinguish from DLP findings in audit/SIEM queries.

### Requirement: Detector produces no findings on benign content

The recognizers SHALL be deterministic regex matchers with no ML model dependency. They SHALL emit zero findings when the input contains none of the configured patterns. False-positive rate target on the test corpus (see `test_services/test_security_hooks_injection.py`) SHALL be ≤ 5% per recognizer on a curated benign dataset (Python tutorial snippets, SQL reference docs, Jinja tutorial content, MDN HTML examples).

### Requirement: Action merging follows the most-restrictive-wins rule

When multiple recognizers fire on the same response, the system SHALL compute a single overall action using the same most-restrictive-wins ordering used by DLP: `block > mask > sanitize > audit > allow`. This mirrors `DLPAction.overall_action` (`services/security/dlp/result.py`) and SHALL be implemented as a shared utility rather than duplicated.

### Requirement: Detector is synchronous and adds negligible latency

Each recognizer SHALL execute synchronously inside the existing post-LLM hook (`OutputSecurityHook.on_post_llm_call`). Per-call latency budget SHALL be ≤ 5ms for a typical 2KB response across all four built-in recognizers combined. Custom-pattern regex execution SHALL respect a configurable per-pattern timeout (default 50ms) and SHALL NOT block the hook on timeout (skip + log warning instead).

### Requirement: Configuration section is opt-in with backward-compatible defaults

`guardrail_config["injection_detection"]` is an optional section. When absent, all four built-in recognizers SHALL default to `enabled=True, action="audit"` (per the safe-default rule above). When explicitly set to `"enabled": False`, the entire capability SHALL be skipped (recognizers not constructed, no overhead).

This is deliberately distinct from `output_security["enabled"]` — disabling `output_security` collapses to NoOpPostLLMHook (kills toxicity/PII/DLP scanning too); disabling `injection_detection` only skips this capability while preserving the rest of the output pipeline.

### Requirement: Findings carry rule_name aligned with SecurityFindingModel schema

Each finding SHALL set `rule_name` on the SecurityFindingModel row to one of:

- `injection_detection.code_python`
- `injection_detection.sql_injection`
- `injection_detection.template_jinja`
- `injection_detection.xss`
- `injection_detection.custom.<n>` (for custom patterns)

This naming convention is forward-compatible with the SIEM collector's `from_security_finding` mapping (`services/security/siem/event.py:197`) — it preserves the dotted `domain.capability` shape already used by the existing DLP rule names.

### Requirement: Detector supports streaming via the existing accumulated-response contract

Streaming LLM responses (`LLMWorker.execute_stream`) accumulate the full response before invoking the post hook (see `runtime/workers/llm_worker.py` execute_stream path, post hook call at line 570). The detector SHALL operate on the full accumulated string, NOT on individual tokens. This is a deliberate alignment with Bedrock Guardrails `ApplyGuardrail` semantics — token-level chunk scanning is a future change (see Deferred items in proposal.md).

#### Scenario: streaming response fires a finding at end-of-stream

- **WHEN** streaming completes and the accumulated response contains `eval(user_input)`
- **THEN** the detector SHALL emit findings at the post-hook call site
- **AND** the channel write SHALL reflect the post-hook action (BLOCK → "I cannot provide that response due to safety policy" replacement; SANITIZE → content replaced).

### Requirement: Future sink-aware extension is reserved by API shape (Deferred)

This capability SHALL reserve a `sink` parameter in the recognizer base class signature (`Recognizer.detect(content: str, *, sink: str | None = None) -> list[Finding]`) so that a future change can introduce sink-aware detection without breaking the recognizer API. The current implementation SHALL treat `sink=None` as the default and SHALL NOT change behavior based on sink values (all current logic is sink-agnostic, content-only).

#### Scenario: future PreToolHook wiring supplies `sink="sql_query"`

- **WHEN** a future change wires the post-LLM hook with `sink="sql_query"` for outputs that flow to `db_query` tool arguments
- **THEN** recognizers SHALL be able to inspect `sink` and adjust severity (e.g., SQL patterns → CRITICAL when sink is sql_query)
- **AND** the current change SHALL NOT modify behavior on this parameter (backward compatible).

### Requirement: Detector integrates into both execution paths

The detector SHALL run inside `OutputSecurityHook.on_post_llm_call` and SHALL be invoked identically from both execution paths:

- Pregel path: `runtime/workers/llm_worker.py` lines 416 (non-streaming) and 570 (streaming)
- Path A direct chat loop: `services/orchestration/agent_execution_port.py:239`

No new wiring SHALL be added to these two call sites — the existing factory (`create_security_hooks`) and assembly (`assemble_guardrails`) handle it via the standard hook pipeline.