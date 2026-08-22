# Tasks: Output-side typed findings (9.1a + 9.2 + shared wiring)

## L0 — Output findings wiring substrate (capability: output-findings-wiring)

> Closes the historical `security_finding_writer` wiring gap. Required substrate for L1 and L2.

- [x] 1. **Introduce `SecurityFindingWriter` class** in `src/hecate/services/security/finding_writer.py`
  - Constructor: `(db, org_id, workspace_id, session_id=None, user_id=None, event_store=None, emit_event=True)`
  - Methods: `async write(...)` and `async write_many(...)`
  - Persistence via `SecurityFindingModel` (already exists at `src/hecate/models/security_finding.py`)
  - EventStore emission via `event_store.append(Event(event_type=EventType.SECURITY_FINDING, payload=...))` — additive, falls back to CUSTOM
  - rule_name convention: `output.dlp.<entity>`, `output.injection_detection.<recognizer>`, `output.prompt_leakage.<category>`
  - Fail-safe: catch exceptions, log WARNING, continue execution
  - Tests: `tests/test_services/test_security_finding_wiring.py::test_writer_persists_finding`, `::test_writer_skips_without_db`, `::test_writer_emits_event`

- [x] 2. **Extend `OutputSecurityHook._write_audit_records`** in `src/hecate/services/security/hooks/output_security.py`
  - Accept either `SecurityFindingWriter` instance or legacy callable
  - For writer instance: call `writer.write(...)` with structured kwargs
  - For legacy callable: call directly with kwargs (backward compat)
  - Tests: `tests/test_services/test_security_hooks.py::test_legacy_callable_writer_compat`

- [x] 3. **Extend `create_security_hooks` factory** in `src/hecate/services/security/hooks/__init__.py`
  - Accept new kwarg `finding_writer: SecurityFindingWriter | None = None`
  - Pass to `OutputSecurityHook(..., security_finding_writer=finding_writer)`
  - Tests: `tests/test_services/test_security_hooks.py::test_factory_threads_writer`

- [x] 4. **Extend `assemble_guardrails` to construct the writer** in `src/hecate/services/security/guardrail_assembly.py`
  - When `(db, event_store, session_id)` are all available, construct `SecurityFindingWriter(db=db, org_id=..., workspace_id=workspace_id, session_id=session_id, event_store=event_store)`
  - org_id lookup: select from `WorkspaceModel.org_id` (or pass through if already provided)
  - Pass to `create_security_hooks(..., finding_writer=writer)`
  - When any of `(event_store, session_id)` missing, omit writer (no behavior change for the historical bug state)
  - Tests: `tests/test_services/test_guardrail_assembly.py::test_assembly_constructs_writer`, `::test_assembly_omits_writer_when_session_missing`

- [x] 5. **Extend EventType enum** in `src/hecate/engine/eventstore.py`
  - Add `INJECTION_DETECTED = "INJECTION_DETECTED"` and `PROMPT_LEAKAGE_DETECTED = "PROMPT_LEAKAGE_DETECTED"`
  - Add inline comment per ADR-030 §1 additive contract
  - Tests: `tests/test_engine/test_eventstore.py::test_injection_detected_falls_back_to_custom_in_unknown_reader`

## L1 — 9.1a Injection Type Detection (capability: injection-detection)

> Builds on L0 writer substrate.

- [x] 6. **Add `Recognizer` base class + `InjectionFinding` dataclass** in `src/hecate/services/security/output/injection_detection/recognizers/base.py`
  - Mirrors `DLPRecognizer` shape from `src/hecate/services/security/dlp/recognizer.py`
  - `Recognizer.detect(content, *, sink=None) -> list[InjectionFinding]` (sink reserved for future)
  - Tests: `tests/test_services/test_security_hooks_injection.py::test_recognizer_api_shape`

- [x] 7. **Implement 4 built-in recognizers** in `src/hecate/services/security/output/injection_detection/recognizers/`
  - `code_python.py` — 3+ patterns (e.g., `eval(`, `exec(`, `__import__`, `compile(`, `subprocess.call(... + ...)`)
  - `sql_injection.py` — 3+ patterns (e.g., `DROP TABLE`, `UNION SELECT`, `;DELETE`, `--\s*$`, `' OR '1'='1`)
  - `template_jinja.py` — 3+ patterns (e.g., `{{ .*config.* }}`, `{% import`, `{% include`, `{{ self.__class__`)
  - `xss.py` — 3+ patterns (e.g., `<script>`, `onerror=`, `javascript:`, `<iframe`, `<svg onload`)
  - Each recognizer class exposes `id`, `patterns` (compiled `re.Pattern`), `severity` (default `high`)
  - Tests: `tests/test_services/test_security_hooks_injection.py::test_<recognizer_id>_fires_on_canonical_pattern` × 4, `::test_<recognizer_id>_no_finding_on_benign_content` × 4

- [x] 8. **Implement custom patterns support** in `src/hecate/services/security/output/injection_detection/custom.py`
  - Accept `guardrail_config["injection_detection"]["custom_patterns"]` list
  - Compile at `create_security_hooks` invocation time, wrap in try/except for malformed regex (log + skip)
  - Apply regex timeout (default 50ms) per pattern — implement via `signal.alarm` for sync or `asyncio.wait_for` for async; given detector is sync, use `signal.alarm` with caveat documented
  - Tests: `tests/test_services/test_security_hooks_injection.py::test_custom_pattern_fires`, `::test_malformed_pattern_skipped`

- [x] 9. **Implement per-type action config + most-restrictive-wins merge** in `src/hecate/services/security/output/injection_detection/scanner.py`
  - Function `scan(content, *, guardrail_config) -> tuple[list[InjectionFinding], DLPAction]`
  - Default action for all 4 built-ins: `audit`
  - Allowed values per type: `audit | block | mask | sanitize`
  - Merge multiple findings' actions via `DLPAction.overall_action`
  - Tests: `tests/test_services/test_security_hooks_injection.py::test_per_type_action_override`, `::test_most_restrictive_wins_merge`

- [x] 10. **Wire detector into `OutputSecurityHook`** in `src/hecate/services/security/hooks/output_security.py`
  - New step `_check_injection(content)` after DLP scan, before returning
  - Calls `injection_detection.scanner.scan(content, guardrail_config=...)`
  - Maps returned `DLPAction` to `GuardrailAction` (block / sanitize / allow)
  - Emits each `InjectionFinding` via `self._security_finding_writer.write(...)`
  - Disabled when `guardrail_config["injection_detection"]["enabled"] is False`
  - Tests: `tests/test_services/test_security_hooks_injection.py::test_hook_integration_blocks_on_code_python`, `::test_hook_integration_audits_on_default`

- [x] 11. **Extend EventType emission for INJECTION_DETECTED** in `src/hecate/engine/eventstore.py`
  - Emit `EventType.INJECTION_DETECTED` with `payload={"recognizer": ..., "entity_type": ..., "severity": ..., "action": ...}`
  - Same emission hook in `OutputSecurityHook._check_injection` (paired with finding write)
  - Tests: `tests/test_services/test_security_hooks_injection.py::test_event_emitted_on_finding`

## L2 — 9.2 System Prompt Leakage Protection (capability: prompt-leakage-protection)

> Builds on L0 writer substrate. Independent from L1.

- [x] 12. **Implement winnowing fingerprint** in `src/hecate/services/security/output/prompt_leakage/fingerprint.py`
  - Functions: `_normalize`, `_hash_token` (blake2b), `fingerprint(text, n=5)`, `_select_minima`, `overlap_ratio`
  - Edge cases: empty input, very short input (< n tokens), whitespace-only
  - Tests: `tests/test_services/test_security_hooks_prompt_leakage.py::test_fingerprint_deterministic`, `::test_fingerprint_handles_short_input`, `::test_fingerprint_handles_whitespace`

- [x] 13. **Implement severity classifier** in `src/hecate/services/security/output/prompt_leakage/severity.py`
  - 4 categories: `secrets` (CRITICAL), `rules` (HIGH), `roles` (HIGH), `persona` (LOW)
  - Heuristic regex hints per category (see design.md Decision section)
  - Function `classify_severity(matched_substring, context_window) -> tuple[str, str]` (severity, category)
  - Tests: `tests/test_services/test_security_hooks_prompt_leakage.py::test_classify_secrets`, `::test_classify_rules`, `::test_classify_roles`, `::test_classify_persona`

- [x] 14. **Implement scanner facade** in `src/hecate/services/security/output/prompt_leakage/scanner.py`
  - Function `scan(response_content, system_prompt_content, *, threshold=0.20, action="block") -> tuple[Finding | None, GuardrailAction]`
  - Returns `(None, ALLOW)` if `system_prompt_content` is empty (degraded mode)
  - Returns `(Finding, BLOCK | SANITIZE)` if overlap > threshold
  - Fail-open with audit event if fingerprint compute raises
  - Tests: `tests/test_services/test_security_hooks_prompt_leakage.py::test_scan_no_leak_benign`, `::test_scan_persona_leak_low_severity`, `::test_scan_secrets_leak_critical`, `::test_scan_threshold_tuning`, `::test_scan_fail_open_on_compute_error`

- [x] 15. **Implement SANITIZE redaction** in `src/hecate/services/security/output/prompt_leakage/redactor.py`
  - Function `redact(content, baseline_fingerprint) -> str` — replaces 5-token windows + 5-token context on each side of a matched fingerprint with `<REDACTED>`
  - Tests: `tests/test_services/test_security_hooks_prompt_leakage.py::test_redact_replaces_matched_windows`

- [x] 16. **Wire detector into `OutputSecurityHook`** in `src/hecate/services/security/hooks/output_security.py`
  - New step `_check_prompt_leakage(content, messages)` after injection detection
  - Builds baseline fingerprint from `messages[0]["content"]` (cached by `(session_id, agent_id, system_prompt_hash)` for streaming reuse)
  - Calls `prompt_leakage.scanner.scan(...)`
  - Maps returned action to `GuardrailAction.BLOCK` or `GuardrailAction.SANITIZE` with `modified_data`
  - Emits finding via `self._security_finding_writer.write(...)`
  - Emits `EventType.PROMPT_LEAKAGE_DETECTED` event (paired with finding write)
  - Disabled when `guardrail_config["prompt_leakage"]["enabled"] is False`
  - Tests: `tests/test_services/test_security_hooks_prompt_leakage.py::test_hook_integration_blocks_on_persona_leak`, `::test_hook_integration_sanitizes_on_threshold_match`, `::test_hook_integration_streaming_caches_fingerprint`

- [x] 17. **Extend EventType emission for PROMPT_LEAKAGE_DETECTED** in `src/hecate/engine/eventstore.py`
  - Already added INJECTION_DETECTED + PROMPT_LEAKAGE_DETECTED in task #5; this task only verifies the prompt leakage emission code path
  - Tests: `tests/test_services/test_security_hooks_prompt_leakage.py::test_event_emitted_on_finding`

## L3 — Documentation, catalog sync, verification

- [x] 18. **Update `docs/design/security-architecture.md`**
  - Expand SS3 (9.1a Injection Type Detection) section with: 4 recognizers, default action AUDIT, custom patterns, regex timeout
  - Expand SS4 (9.2 System Prompt Leakage Protection) section with: winnowing fingerprint, OWASP LLM07 4 example attack types, severity tiers, action merge
  - Add cross-reference to `docs/research/2026-08-output-guardrails-comparison.md`

- [x] 19. **Update `docs/design/adr/026-security-shield-enhancement.md`**
  - SS3 decision: regex recognizer registry (not YARA / not Colang), default action AUDIT, reference Bedrock Standard tier "code elements"
  - SS4 decision: winnowing fingerprint (n=5), default action BLOCK, threshold 0.20, OWASP LLM07 mapping, semantic similarity v2 reserved seam
  - Update Status line from "Proposed" to "Accepted" (or mark this change's deliverable section)

- [x] 20. **Update `docs/features/feature-catalog.md`**
  - 9.1a row: remove "Planned enhancement (SS3)" language, mark ✅
  - 9.2 row: remove "Planned enhancement (SS4)" language, mark ✅
  - P3 progress: 85/87 (was 83/87)
  - "Remaining 4 close-out items" → "Remaining 2 close-out items: 5.4b MCP Streamable HTTP Server 端 / 6.27 Browser Automation"
  - Add references to this change's archive directory

- [x] 21. **Update `docs/features/roadmap.md`**
  - Mirror catalog changes for 9.1a / 9.2 completion

- [x] 22. **Update `docs/design/positioning.md`** — **DEFERRED to `/opsx:archive` step**. AGENTS.md mandates positioning.md sync at archive time, not apply time. The doc has no per-feature rows to update; sync will scan for any qualitative competitive claims that need refreshing.

- [x] 23. **Verify before committing (run ALL of these, AGENTS.md mandate)**
  - `ruff check src/hecate/ tests/` → 0 errors ✅ (fixed 5 format issues + 15 lint errors in new code)
  - `ruff format --check src/ tests/` → 0 errors ✅
  - `mypy src/` → 0 errors ✅ (555 files)
  - `python -m pytest tests/ -q` → all green ✅ (3638 passed, 27 skipped, 1 xfailed)
  - **STATUS**: Completed in worktree dev environment (uv venv + Python 3.13). Real defects found and fixed during verification:
    - `DLPAction.SANITIZE` did not exist — added member with severity ordering `BLOCK > MASK > SANITIZE > AUDIT > ALLOW` per injection-detection spec; updated legacy `test_has_four_members` contract test to the 5-member contract.
    - Prompt-leakage fingerprint missed single-token secret reproduction (OWASP LLM07): added secret-like unigram supplementation (punctuation-stripped core hashing) to `fingerprint()` + `find_matched_indices()`.
    - Severity classifier matched bare "you are a(n)" as roles; aligned hints with spec (`<role>`, `permission:`).
    - Fixed `test_overlap_ratio` candidate to share ≥5 tokens (winnowing detection floor per spec).

- [x] 24. **Commit + push workflow per AGENTS.md**
  - Pre-commit hooks run all 4 checks; do NOT use `--no-verify`
  - User confirmed push in chat (AGENTS.md mandate satisfied)

## Deferred follow-ups (tracked, not in this change)

These are explicitly registered in `proposal.md` § "Deferred to Follow-up Changes". Each one will spawn its own OpenSpec change when prioritized:

- D1. **9.1a sink-aware extension** — PreToolHook wiring for `sink` parameter
- D2. **9.2 semantic similarity v2** — embedding-based paraphrase detection
- D3. **Token-level streaming chunk scanning** — Bedrock `ApplyGuardrail`-style mid-stream guard
- D4. **OpenClaw source-of-truth refresh** — Hecate `feature-catalog.md`引用重核 (separate repo issue)
- D5. **Watsonx / Vertex ADK / Palantir AIP / AgentArts / openJiuwen / Manus / CatPaw 安全特性补全调研** — 竞争对标完整度

## Deferred follow-ups (tracked, not in this change)

These are explicitly registered in `proposal.md` § "Deferred to Follow-up Changes". Each one will spawn its own OpenSpec change when prioritized:

- D1. **9.1a sink-aware extension** — PreToolHook wiring for `sink` parameter
- D2. **9.2 semantic similarity v2** — embedding-based paraphrase detection
- D3. **Token-level streaming chunk scanning** — Bedrock `ApplyGuardrail`-style mid-stream guard
- D4. **OpenClaw source-of-truth refresh** — Hecate `feature-catalog.md`引用重核 (separate repo issue)
- D5. **Watsonx / Vertex ADK / Palantir AIP / AgentArts / openJiuwen / Manus / CatPaw 安全特性补全调研** — 竞争对标完整度