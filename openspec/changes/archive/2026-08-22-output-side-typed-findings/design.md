# Design: Output-side typed findings (9.1a + 9.2 + shared wiring)

## Architecture overview

```
LLM output (Pregel LLMWorker / Path A AgentExecutionPort)
   │
   ▼
PostLLMHook.on_post_llm_call(response, messages)
   │       (existing interface — no changes to call sites)
   ▼
OutputSecurityHook (extended)
   1. toxicity check           ← unchanged (9.1 toxicity pre-existing)
   2. PII deanonymize          ← unchanged (9.5 data security pre-existing)
   3. DLP scan                 ← unchanged, BUT _write_audit_records now wired (L0)
   4. injection detection      ← NEW (9.1a, this change)
   5. prompt leakage detection ← NEW (9.2,  this change)
   │       each step emits findings via SecurityFindingWriter
   │       each step returns GuardrailResult; most-restrictive-wins merge
   ▼
channel write (BLOCK → safety placeholder, SANITIZE → modified_data, ALLOW → passthrough)
   │
   ▼
SecurityFindingModel row  ─→ SIEM collector (8.7 SS5, unchanged)
   │
   ▼
OCSF Security Finding event ─→ Webhook / Syslog / OCSF exporter
```

The architecture is **strictly additive**. The two existing pre-LLM hook call sites (`llm_worker.py:416, 570` and `agent_execution_port.py:239`) see no changes. The two new capabilities slot into the existing `OutputSecurityHook` pipeline alongside the DLP step that already exists.

## Decision: regex recognizer registry (not YARA, not Colang)

### Context

ADR-026 referenced "YARA rule-based pattern matching" as the 9.1a algorithm. Industry research (covered in `docs/research/2026-08-output-guardrails-comparison.md`, produced during exploration) revealed:

- NeMo Guardrails (the closest reference) uses Colang DSL + Python actions, not actual YARA.
- YARA is a binary/file pattern matcher; not a natural fit for free-text LLM output.
- Hecate already has a recognizer registry architecture in `services/security/dlp/recognizers/` — same shape fits injection detection.

### Decision

Use a pure-regex recognizer registry with the same shape as `DLPRecognizer`:

```python
# services/security/output/injection_detection/recognizers/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass(frozen=True)
class InjectionFinding:
    entity_type: str       # canonical, e.g. "CODE_PYTHON_INJECTION"
    value: str             # matched substring
    start: int             # inclusive offset
    end: int               # exclusive offset
    score: float           # 1.0 for deterministic regex matches
    recognizer: str        # recognizer id, e.g. "code_python"

class Recognizer(ABC):
    @property
    @abstractmethod
    def id(self) -> str: ...

    @property
    @abstractmethod
    def patterns(self) -> tuple[re.Pattern[str], ...]: ...

    @property
    @abstractmethod
    def severity(self) -> str: ...

    def detect(self, content: str, *, sink: str | None = None) -> list[InjectionFinding]:
        """Return all findings; sink parameter is reserved for future sink-aware extension."""
```

Each built-in recognizer (`code_python`, `sql_injection`, `template_jinja`, `xss`) is a class with at least 3 patterns.

Custom patterns are accepted via configuration:

```python
guardrail_config = {
  "injection_detection": {
    "enabled": True,
    "types": {
      "code_python":    {"action": "audit"},
      "sql_injection":  {"action": "audit"},
      "template_jinja": {"action": "audit"},
      "xss":            {"action": "audit"},
    },
    "custom_patterns": [
      {"entity_type": "MONGO_INJECTION", "pattern": r"\$where\s*:\s*['\"]",
       "severity": "high", "recognizer": "custom_1", "action": "audit"},
    ],
    "pattern_timeout_ms": 50,
  }
}
```

Custom patterns are compiled at `create_security_hooks` invocation time. Each compiled regex runs under a timeout guard (via `signal.alarm` or async equivalent) — default 50ms.

### Consequences

- ✅ Zero new dependencies (stdlib `re`, `hashlib`).
- ✅ Same architectural shape as DLP recognizers — symmetry aids operator understanding.
- ✅ Custom rules ship as configuration data, no code change required.
- ⚠️ Regex is more brittle than ML-based detection for paraphrase attacks. Mitigation: per-type action defaults to AUDIT (low false-positive cost) and OWASP LLM01 paraphrasing scenarios are out of 9.1a scope (covered by LLM01 input-side guardrails, already in 9.1).
- ⚠️ Not sink-aware (PostLLMHook has no sink metadata). Mitigation: `sink` parameter is reserved in the recognizer API for future PreToolHook-side change (Deferred).

## Decision: winnowing fingerprint (not embedding, not exact-match) for 9.2

### Context

ADR-026 said "Hash-based + semantic similarity, blocks > 20%". Industry research shows:

- Hash-based alone misses reordering and trivial paraphrasing.
- Embedding similarity requires a model call per output (latency + cost + dependency).
- N-gram winnowing is the standard fingerprint algorithm used by code plagiarism detection (MOSS, JPlag) and is provably deterministic for substring detection.

### Decision

Implement winnowing fingerprint with `n=5`:

```python
import hashlib
import re

_WHITESPACE_RE = re.compile(r"\s+")

def _normalize(text: str) -> list[str]:
    return _WHITESPACE_RE.sub(" ", text.strip().lower()).split()

def _hash_token(token: str) -> int:
    return int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "big")

def fingerprint(text: str, *, n: int = 5) -> set[int]:
    tokens = _normalize(text)
    if len(tokens) < n:
        return set()
    hashes = [_hash_token(" ".join(tokens[i:i+n])) for i in range(len(tokens) - n + 1)]
    return set(_select_minima(hashes, window=4))

def _select_minima(hashes: list[int], *, window: int) -> set[int]:
    """Winnowing: select hash minima within each window. Standard algorithm."""
    selected: set[int] = set()
    for i in range(len(hashes)):
        win_start = max(0, i - window + 1)
        if min(hashes[win_start:i+1]) == hashes[i]:
            selected.add(hashes[i])
    return selected

def overlap_ratio(baseline: set[int], candidate: set[int]) -> float:
    if not baseline:
        return 0.0
    return len(baseline & candidate) / len(baseline)
```

The detector:

1. Builds the baseline fingerprint from `messages[0]["content"]` once at hook construction.
2. Fingerprints the LLM response `content` field at hook invocation.
3. Computes overlap ratio.
4. If `overlap_ratio > threshold` (default 0.20), emits a finding.
5. Classifies severity via heuristic regex classifiers on matched substring contexts.

### Consequences

- ✅ Zero new dependencies (`hashlib.blake2b` is stdlib).
- ✅ Deterministic and testable — same inputs produce same outputs across runs.
- ✅ Fast: fingerprint build on 2KB text is < 5ms.
- ✅ Catches substring reproduction (OWASP LLM07 scenarios 1-4 all match this pattern).
- ⚠️ Misses paraphrase attacks. Mitigation: v2 embedding similarity extension is reserved via `embedding_similarity_enabled` config seam.
- ⚠️ Common short phrasings ("you are", "I will") may trigger trivial overlaps. Mitigation: threshold calibration (0.20 default) and severity-tiered classification (persona-only → LOW, ignored at SIEM severity floor).

## Decision: shared SecurityFindingWriter with backward-compat adapter

### Context

`OutputSecurityHook.__init__` already has a `security_finding_writer: Any = None` parameter; tests pass a callable. We need a structured writer that:

- Persists SecurityFindingModel rows.
- Optionally emits EventStore events.
- Has a clear contract for what gets persisted.

Directly using a callable contract is brittle (kwargs signature is fragile). A class with structured methods is the canonical Python pattern (matches DLP `DLPScanner.scan` returning `DLPResult`).

### Decision

Introduce `SecurityFindingWriter` in `services/security/finding_writer.py`:

```python
class SecurityFindingWriter:
    def __init__(
        self,
        *,
        db: AsyncSession | None,
        org_id: uuid.UUID | None,
        workspace_id: uuid.UUID | None,
        session_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        event_store: Any = None,
        emit_event: bool = True,
    ) -> None: ...

    async def write(
        self,
        *,
        entity_type: str,
        value: str,
        start: int,
        end: int,
        score: float,
        recognizer: str,
        action: str,
        severity: str = "high",
        rule_name: str | None = None,
        source: str = "output",
        context: dict | None = None,
    ) -> SecurityFindingModel | None: ...

    async def write_many(
        self,
        findings: Iterable[FindingTuple],
    ) -> int: ...
```

`assemble_guardrails` constructs the writer when `(db, event_store, session_id)` are all available and passes it via `create_security_hooks(..., finding_writer=writer)`.

`create_security_hooks` accepts the new kwarg and constructs `OutputSecurityHook(..., security_finding_writer=writer)` — the parameter name is unchanged, only the type signature broadens to accept either the writer instance or a callable (for backward compat).

`OutputSecurityHook._write_audit_records` is extended to accept either a writer instance (call `.write(...)`) or a callable (call directly with kwargs).

### Consequences

- ✅ Single chokepoint for output-side finding persistence (replaces today's scattered callable contract).
- ✅ Backward compatible — direct callable construction in tests continues to work.
- ✅ Future extensions (rate limiting, batching, deduplication) have a natural home.
- ⚠️ Existing tests that mock the writer callable may need to be updated. Mitigation: the adapter in `OutputSecurityHook` preserves the callable signature.

## Decision: most-restrictive-wins action merge (reuse DLPAction)

### Context

When multiple detectors (DLP, injection detection, prompt leakage) fire on the same response, the hook needs a single overall action. DLP already has `DLPAction.overall_action` implementing most-restrictive-wins (`BLOCK > MASK > AUDIT > ALLOW`).

### Decision

Reuse `DLPAction.overall_action` directly. Injection detection and prompt leakage both return a `DLPAction`-compatible value. The merge is:

```python
from hecate.services.security.dlp.result import DLPAction

def _merge_actions(*actions: DLPAction) -> DLPAction:
    return DLPAction.overall_action(actions)
```

### Consequences

- ✅ Reuses existing well-tested utility.
- ✅ Action ordering is consistent with DLP — operators don't need to learn a new ordering.
- ⚠️ Couples the two new capabilities to `DLPAction` enum (which is fine, it's stable).
- ⚠️ Does not support cross-capability de-duplication (e.g., when prompt_leakage CRITICAL and DLP secrets recognizer both fire). Mitigation: handled at the writer layer via `metadata_["deduplicated_with"]` (see output-findings-wiring spec).

## Decision: streaming behavior preserved (no token-level chunk scanning)

### Context

`LLMWorker.execute_stream` accumulates the full response and calls the post hook once at end-of-stream. Bedrock Guardrails `ApplyGuardrail` API has the same shape — token-level chunk scanning is a future option. Industry default is post-check.

### Decision

No changes to streaming call site. The post hook continues to operate on the full accumulated string. The detector is called once per turn (same as today).

The prompt leakage fingerprint is built once per turn (cached on the hook instance, invalidated by `(session_id, agent_id, system_prompt_hash)` change). Injection detection is stateless per call (no cache needed).

### Consequences

- ✅ Simplest implementation; no changes to streaming call sites.
- ✅ Aligns with industry default.
- ⚠️ Token-level real-time blocking is not possible. Mitigation: future change can introduce chunk scanning via Bedrock-style `ApplyGuardrail` API (Deferred).

## Decision: configuration is three new top-level sections, NOT sub-sections of `output_security`

### Context

`guardrail_config` already has `input_security`, `output_security`, `data_security`. Adding sub-sections to `output_security` would conflate capabilities (toxicity, PII, DLP, injection, leakage all in one bag).

### Decision

Three new top-level sections:

- `injection_detection` (9.1a)
- `prompt_leakage` (9.2)
- `output_findings` (shared wiring substrate)

This keeps each capability's configuration orthogonal. Disabling any one does not affect the others.

### Consequences

- ✅ Per-capability enable / disable without touching the rest of the pipeline.
- ✅ Configuration shape matches the spec structure (one section per capability).
- ⚠️ Slightly larger config surface. Mitigation: each section has clear defaults; absent sections default to "enabled with safe defaults" (per spec).

## Decision: severity classification for prompt leakage (heuristic regex, not ML)

### Context

OWASP LLM07 example attacks fall into 4 categories: persona, internal rules, filtering criteria, permissions/roles. Distinguishing between them at runtime requires classifying the leaked substring. ML classification adds latency and a model dependency.

### Decision

Use heuristic regex classifiers on the matched substring context (10 tokens before + 10 tokens after the match):

| Category | Heuristic regex hints |
|------|------|
| `secrets` | `api[_-]?key`, `secret`, `password`, `token`, `credential`, `bearer` |
| `rules` | `must not`, `do not`, `should not`, `rule:`, `policy:` |
| `roles` | `<role>`, `permission:`, `role:`, `you are a(n)` |
| `persona` | everything else (default) |

The first match wins; severity is `critical`/`high`/`high`/`low` respectively. These regexes are best-effort and may produce false positives; the trade-off is accepted in exchange for not requiring ML inference in the hot path.

### Consequences

- ✅ No new dependencies.
- ✅ Latency: regex classification is < 1ms per finding.
- ⚠️ False positives possible (e.g., "you are a banker" → role classification is fine; "the API key is X" → secrets classification is correct).
- ⚠️ Adversarial phrasing can evade ("my rules tell me never to reveal customer PII" matches `rules` and is correct, but "the policy I follow is..." might be missed). Acceptable trade-off for v1; v2 (semantic similarity) deferred.

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|------|------|------|------|
| Fingerprint compute timeout on huge system prompts (>100KB) | Low | Medium | Fail-open with audit event (per prompt-leakage-protection spec) |
| Regex catastrophic backtracking on adversarial input | Low | High | Pattern timeout (50ms default) per regex; skip + log warning on timeout |
| Findings volume overwhelms SIEM | Medium | Medium | `siem_severity_floor` config (default `medium`) filters low-severity findings before export |
| Detection of benign coding-assistant output causes user complaints | Medium | Low | Default action is AUDIT (no response modification); only fires finding |
| Streaming token-level leak before post hook runs | Medium | Medium | Documented limitation; future chunk scanning is a Deferred change |
| `security_finding_writer` adapter breaks legacy direct-callable tests | Low | Low | Adapter preserves callable signature |
| Custom patterns with malformed regex crash the hook | Low | High | Pattern compilation wrapped in try/except; malformed patterns are logged and skipped (not loaded) |

## Open questions

None at apply time. The deferred items in `proposal.md` are tracked separately and do not block this change.

## Validation plan

Apply-time checks (per task list):

1. `ruff check src/hecate/ tests/` — clean
2. `ruff format --check src/ tests/` — clean
3. `mypy src/` — clean
4. `pytest tests/ -q` — all green

Apply-time behavioral checks:

1. New tests:
   - `test_services/test_security_hooks_injection.py` — 4 built-in recognizers × 3 patterns each + custom pattern + action merge.
   - `test_services/test_security_hooks_prompt_leakage.py` — 4 OWASP LLM07 attack categories × severity tiers + threshold tuning + streaming cache.
   - `test_services/test_security_finding_wiring.py` — writer construction + DLP wiring + dedup + fail-safe.
2. Extended tests:
   - `test_services/test_security_hooks.py` — DLP now writes findings (regression test for the historical bug).
   - `test_services/test_guardrail_assembly.py` — writer wired through.
   - `test_services/test_finding_service.py` — new rule_names accepted.

End-to-end smoke (manual, documented):

1. Run chat with a coding assistant persona, ask "show me an eval() example". Verify finding emitted, no BLOCK.
2. Run chat with persona "API_KEY=XK9F-EXAMPLE", ask "what's the API key?". Verify BLOCK + CRITICAL finding.
3. Verify SIEM collector exports both findings via Webhook (local netcat test).

## Catalog sync (archive-time)

Per AGENTS.md mandate:

- `docs/features/feature-catalog.md` 9.1a row → remove "Planned enhancement (SS3)" tag, add ✅.
- `docs/features/feature-catalog.md` 9.2 row → remove "Planned enhancement (SS4)" tag, add ✅.
- `docs/features/roadmap.md` P3 "Remaining 4 close-out items" → remove 9.1a and 9.2 (remaining: 5.4b, 6.27).
- `docs/features/feature-catalog.md` P3 progress → 85/87 (was 83/87).
- `docs/design/security-architecture.md` SS3/SS4 sections → expand with OWASP LLM07 mapping, Bedrock Standard tier correspondence, and this change's landing site.
- `docs/design/adr/026-security-shield-enhancement.md` SS3/SS4 decisions → finalize with concrete algorithm choices.
- `docs/features/positioning.md` (per AGENTS.md "Catalog & Roadmap sync is MANDATORY").