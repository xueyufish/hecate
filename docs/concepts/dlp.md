# Data Loss Prevention (DLP)

Agent outputs leave your network — to users, to other agents, to MCP tool servers, to upstream LLMs. Without an outbound gate, an agent that has read a customer record, an AWS key, or an internal document can leak that content in its next response. **Data Loss Prevention (DLP)** is Hecate's unified outbound detection engine: it scans content at every trust boundary for sensitive data (PII, secrets, custom patterns) and applies a configurable per-entity policy (`ALLOW` / `MASK` / `BLOCK` / `AUDIT`).

DLP is the outbound complement to the [guardrail hooks](guardrails.md). Where hooks are the *interception points*, DLP is the *detection and policy engine* those hooks (and the MCP client) share.

---

## Why a dedicated DLP engine

Hecate previously had two independent, hard-coded detectors — `PIIAnonymizer` (five regex patterns) and `LLMGuardScanner` (wrapping the `llm-guard` library). Both were called directly by the security hooks with no policy abstraction, no per-entity configuration, and no scanning of MCP tool responses (which flowed straight into the agent's context). That left three gaps:

1. **No policy layer** — you could not say "mask email but block secrets."
2. **No coverage of MCP responses** — the fastest-growing source of untrusted inbound content was unscanned.
3. **No graduated response** — the only options were mask everything or nothing; there was no "detect and log, don't block" mode for safe rollouts.

The DLP engine (shipped in the outbound-dlp-engine change, Aug 2026) closes all three with a three-layer architecture modelled on traditional enterprise DLP (Microsoft Purview, Symantec, Forcepoint, Google Cloud DLP).

---

## The three-layer architecture

```
Content in → [ 1. DETECTION ] → [ 2. POLICY ] → [ 3. ENFORCEMENT ] → Content out
              Recognizers       PolicyResolver    Action applied
              (what's there?)   (what to do?)     (do it)
```

### 1. Detection — Recognizers

A **Recognizer** detects one category of sensitive data. Four implementations ship:

| Recognizer | Detects | Dependency |
|-----------|---------|------------|
| `RegexRecognizer` | PII patterns (SSN, credit card with Luhn check, email, phone, passport, China ID) | always available |
| `SecretsRecognizer` | Cloud keys, private keys, API tokens (wraps `detect-secrets`) | `detect-secrets` |
| `PresidioRecognizer` | Named-entity recognition (names, addresses, orgs) via Microsoft Presidio + spaCy | optional — `[security]` extra |
| `DictionaryRecognizer` | Custom term lists (case-insensitive whole-word) | always available |

Presidio is an **optional** dependency because it pulls in spaCy + models (large). Without it, the regex + secrets + dictionary recognizers still cover the majority of cases; Presidio adds ML-based NER for unstructured names/addresses.

### 2. Policy — `DLPPolicyResolver`

Detection tells you *what* is in the content; policy decides *what to do about it*. Policies resolve through a **three-level scope override**:

```
org  →  workspace  →  agent      (most specific wins)
```

Each policy targets an entity type (e.g. `EMAIL`, `AWS_KEY`), a direction (input/output), and an action. A lower scope can override a higher one **unless** the higher policy sets `is_locked: true` — the hard-constraint flag a security team uses to set red lines that workspaces and agents cannot relax (for example, `secrets → BLOCK` is locked by default).

### 3. Enforcement — the four actions

| Action | Meaning |
|--------|---------|
| `ALLOW` | No change; content passes through |
| `MASK` | Replace detections with tokens (e.g. `[EMAIL_1]`) |
| `BLOCK` | Stop the content entirely (the superstep aborts, or the stream stops) |
| `AUDIT` | Detect and log, but do **not** alter or block — for safe graduated rollouts |

When multiple policies match, the **strictest** action wins. `AUDIT` is the key to the recommended rollout: start a rule in `AUDIT` to measure false-positive rate, promote to `MASK`, then `BLOCK`.

---

## The five trust boundaries

DLP scans at five points — the only places where untrusted content crosses into or out of the agent. All five share a single `DLPScanner` instance (one recognizer registry, one policy resolver).

| # | Boundary | What is scanned | Gate |
|---|----------|-----------------|------|
| 1 | **Input (PreLLM)** | User / inbound messages before the LLM sees them | `InputSecurityHook` (secrets detection delegated to DLP) |
| 2 | **Output (PostLLM)** | LLM responses after deanonymization | `OutputSecurityHook` applies DLP as the egress policy |
| 3 | **Tool result (PostTool)** | Tool outputs before they enter context | `ToolResultSecurityHook` delegates to DLP |
| 4 | **Tool arguments (PreTool)** | Tool call arguments | Pre-tool DLP scan |
| 5 | **MCP response (egress)** | MCP tool-server responses before they reach the agent | `DLPEgressFilter` in `HecateMCPClient.call_tool()` |

Boundary 5 is the differentiator: most agent platforms scan LLM I/O but **not** MCP tool responses. Because MCP is Hecate's primary tool-integration surface, scanning it closes the largest untrusted-inbound gap.

Each boundary can be toggled independently via env vars (`DLP_INPUT_HOOK_ENABLED`, `DLP_OUTPUT_HOOK_ENABLED`, `DLP_TOOL_RESULT_HOOK_ENABLED`, `DLP_MCP_RESPONSE_FILTER_ENABLED`) — see [Environment Variables](../reference/env-vars.md).

---

## Streaming output

LLM responses often stream token-by-token. Sensitive data can span token boundaries, so a single end-of-stream scan is not enough. The `StreamingDLPWrapper` does **incremental** scanning:

- a 300-character buffer with 10-character overlap catches cross-boundary patterns as they accumulate;
- `BLOCK`-class detections stop the stream immediately (zero leakage);
- `MASK`-class detections are corrected with a follow-up message at stream end (in v1 a brief leak of mask-class content is the accepted trade-off for simplicity — secrets and other BLOCK-class content never leak);
- a final full scan at stream end is the backstop.

The buffer/overlap sizes (`DLP_STREAM_BUFFER_SIZE`, `DLP_STREAM_OVERLAP`) and whether the final scan runs (`DLP_STREAM_FINAL_SCAN`) are configurable.

---

## Findings and feedback

Every DLP detection produces a **`SecurityFinding`** (reusing the shared `SecurityFindingModel`, with `rule_name` prefixed `dlp:`). Findings carry org / workspace / user / agent / severity, so they flow through the same SIEM pipeline as the other security events — see [Guardrails and Hooks](guardrails.md#from-hook-events-to-the-siem-pipeline).

Because DLP detectors (especially `detect-secrets`) produce false positives, each finding accepts **feedback**:

```
POST /api/security/findings/{finding_id}/feedback
{ "feedback": "false_positive", "feedback_comment": "test credential in staging" }
```

`feedback` is either `true_positive` or `false_positive`. Collected feedback guides rule tuning — recurring false positives on an entity type are a signal to move that rule from `BLOCK` down to `AUDIT` or to add a targeted `ALLOW`.

---

## Default rules and graduated rollout

On first boot (when `DLP_ENABLED=true`), the migration auto-creates three default policies:

| Entity | Action | Locked? | Rationale |
|--------|--------|---------|-----------|
| secrets (`AWS_KEY`, `PRIVATE_KEY`, …) | `BLOCK` | yes (locked) | Hard red line — never relaxable |
| PII (SSN, credit card, passport, …) | `MASK` | no | Mask by default; workspaces may override |
| `EMAIL` | `AUDIT` | no | Detect-and-log only; promote to MASK/BLOCK once false-positive rate is known |

This gives you a safe day-one default: secrets are blocked immediately, PII is masked, and the noisier detectors run in audit mode so you can measure them before enforcing. The recommended path is the four-phase rollout in the design doc: deploy → monitor audit findings → tighten rules → enable strict mode.

---

## Disabling and rollback

DLP fails **open** by default (unknown entity types pass through) so a misconfiguration cannot lock up your agents. To disable entirely:

```dotenv
DLP_ENABLED=false
```

All hooks then skip the DLP scanner and fall back to the legacy `PIIAnonymizer` behaviour (compatibility mode). The policy tables are retained across enable/disable cycles, so re-enabling picks up the same configuration. Individual boundaries can be toggled independently without disabling the whole engine.

---

## Further reading

- [Guardrails and Hooks](guardrails.md) — the four hook types DLP plugs into
- [Security Architecture](../design/security-architecture.md) — the full security design
- [Environment Variables](../reference/env-vars.md#data-loss-prevention) — every `DLP_*` setting
- [REST API — security findings](../reference/rest-api.md#guardrails-and-security) — the findings + feedback endpoints
