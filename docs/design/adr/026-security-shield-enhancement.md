# ADR-026: Security Shield Enhancement Architecture

> **Status**: Proposed
> **Date**: 2026-07-02

## Context

Hecate's Security Shield provides engine-level guardrail hooks (Pre/Post LLM/Tool), PII anonymization, LLM Guard, 4-level risk authorization, Docker sandbox, and compliance framework. Competitive analysis against OWASP Top 10 for Agentic Applications (ASI01-ASI10, 2026), SafeAgent, AgentShield, Cisco AI Defense, Lakera Red, Promptfoo, and NeMo Guardrails v0.22 revealed 6 gaps:

| Gap | Description | Type | Priority |
|-----|-------------|------|----------|
| SS1 | **Agent Runtime Protection** — stateful runtime security monitoring across execution trajectory | New Feature | P4 (9.11) |
| SS2 | **Automated Continuous Red Teaming** — CI/CD-integrated adversarial testing | New Feature | P4 (7.10) |
| SS3 | **Injection Type Detection** — code/SQL/template/XSS detection for downstream systems | 9.1a Enhancement | P4 |
| SS4 | **System Prompt Leakage Protection** — OWASP LLM07:2025 | 9.2 Enhancement | P4 |
| SS5 | **Security Event SIEM Pipeline** — structured export to enterprise SIEM | 8.7 Enhancement | P4 |
| SS6 | **Multi-Agent Trust Verification** — runtime trust scoring (ASI03/07/09) | 2.10a Enhancement | P4 |

## Decision

### 1. Agent Runtime Protection (SS1/9.11) — Stateful Session-Level Security

Extend per-call Guardrail Hooks with a **stateful runtime security layer** that maintains persistent session state:

```
Per-call Guardrail Hooks (existing, stateless)
    │
    ▼
┌───────────────────────────────────────────────┐
│  Stateful Runtime Security Layer (NEW)         │
│  Session state persists across supersteps      │
│                                                │
│  ┌──────────────────────────────────────────┐ │
│  │ Goal Drift Detector                      │ │
│  │ Cosine distance: current action vs       │ │
│  │ original task goal. Alert if > threshold │ │
│  └──────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────┐ │
│  │ Tool Chain Escalation Detector           │ │
│  │ Forbidden sequence detection:            │ │
│  │ web_search→write_file→exec = exfiltration│ │
│  └──────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────┐ │
│  │ Memory Poisoning Detector                │ │
│  │ Z-score anomaly on memory writes         │ │
│  └──────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────┐ │
│  │ Behavioral Anomaly Scorer                │ │
│  │ Agent DNA fingerprint vs baseline        │ │
│  └──────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────┐ │
│  │ Rogue Agent Detector                     │ │
│  │ Permission boundary violations           │ │
│  └──────────────────────────────────────────┘ │
└───────────────────────────────────────────────┘
```

**Design principle**: Separation of execution governance (runtime controller) from semantic risk reasoning (decision core). The runtime controller mediates actions; the decision core evaluates safety over accumulated session state.

### 2. Automated Continuous Red Teaming (SS2/7.10) — CI/CD-Integrated

```
CI/CD Pipeline
    │
    ▼
┌───────────────────────────────────────────────┐
│  Attack Generator                              │
│  Context-aware: reads agent config, tools,     │
│  knowledge bases, business rules               │
│  Generates 50+ vulnerability types             │
│  ├── Prompt injection (direct/indirect)        │
│  ├── Jailbreaks (roleplay/encoding/crescendo)  │
│  ├── Data leakage (PII/secrets/source)         │
│  ├── Business rule violations                  │
│  ├── Insecure tool use (BOLA/BFLA)             │
│  ├── Toxic content                             │
│  └── Agent attacks (goal hijack/mem poisoning) │
└───────────────────┬───────────────────────────┘
                    │
                    ▼
┌───────────────────────────────────────────────┐
│  Multi-Turn Attack Executor                    │
│  Simulates real attacker across conversation   │
│  turns. Adapts strategy based on responses.    │
└───────────────────┬───────────────────────────┘
                    │
                    ▼
┌───────────────────────────────────────────────┐
│  Vulnerability Report                          │
│  Severity scoring + remediation guidance       │
│  PR comment integration                        │
│  Pass/fail gate decision                       │
└───────────────────────────────────────────────┘
```

### 3-6. Enhancements (SS3-SS6)

**SS3 (Injection Type Detection)**: YARA rule-based pattern matching on LLM outputs before they reach downstream systems (code interpreter, SQL database, template renderer, HTML page). Rules: Python code injection, SQL injection, Jinja template injection, XSS.

**SS4 (System Prompt Leakage)**: Hash-based + semantic similarity comparison of LLM output against system prompt content. Blocks responses reproducing > 20% of system prompt instructions.

**SS5 (SIEM Pipeline)**: Converts internal security events to CEF/LEEF format. Real-time streaming via syslog/HTTPS webhook. Event types: DLP_VIOLATION, INJECTION_ATTEMPT, PERMISSION_DENIED, BEHAVIORAL_ANOMALY, TRUST_SCORE_CHANGE.

**SS6 (Multi-Agent Trust)**: Runtime trust score (0-1) per agent interaction: `trust = f(signature_valid, interaction_success_rate, anomaly_score, permission_alignment)`. Scores < 0.3 trigger blocking; 0.3-0.7 trigger privilege degradation.

## Consequences

- **Positive**: Full OWASP ASI Top 10 coverage; stateful protection closes the biggest agent security gap; CI/CD red teaming catches regressions before deployment
- **Negative**: Runtime protection adds ~10-20ms per superstep for state evaluation; red teaming requires CI/CD infrastructure investment

## Related Documents

- [Security Architecture](../security-architecture.md) — Guardrail Hooks foundation
- [ADR-008: Security via Hooks](008-security-via-hooks.md) — Hook architecture
- [ADR-025: Enterprise Foundation Enhancement](025-enterprise-foundation-enhancement.md) — DLP + Vault
- [OWASP Top 10 for Agentic Applications](https://genai.owasp.org/) — ASI01-ASI10 reference
