# Threat Model

Formal threat model for Hecate using the **STRIDE** framework. This document is the authoritative reference for **what threats Hecate is designed to defend against**, **what mitigations exist today**, and **what gaps remain**.

For the security architecture (components, hook types, audit pipeline), see [Security Architecture](security-architecture.md). For the runbook on responding to incidents, see [Security Hardening](../how-to/security-hardening.md). For vulnerability disclosure, see `SECURITY.md` (when published) at the repo root.

---

## Scope and assets

### What's in scope

| Asset | Where it lives | Sensitivity |
|---|---|---|
| User authentication tokens | `HECATE_API_KEYS`, JWT signed secrets | Critical |
| User PII in conversations | Postgres `messages` table, audit log | High |
| Agent configurations (persona, tools, model settings) | Postgres `agents` table | Medium |
| Knowledge base content | Postgres `documents`, Qdrant vectors | High (may contain proprietary info) |
| LLM API keys (OpenAI, Anthropic, etc.) | `.env` / secrets manager | Critical |
| MCP server endpoints | Plugin manifest `entry` field | Medium |
| A2A AgentCard signing keys | `A2A_PRIVATE_KEY_JWK` (when shipped) | High |
| Audit log integrity | Hash chain in `audit_logs` table | High (compliance) |
| Backup content (includes above) | MinIO / S3 | Critical |

### What's NOT in scope

- Hecate's internal source code vulnerabilities (covered by CVE process)
- LLM provider security (use OpenAI / Anthropic security docs)
- Customer's own deployment infrastructure (network, OS, hypervisor)

### Adversary model

We assume the adversary may:

- Be **external** (internet-facing attackers) or **internal** (compromised employee account)
- Have **API access** (any valid key, even with limited permissions)
- Attempt **prompt injection** via any text the LLM ingests (knowledge bases, MCP tool results, A2A artifacts)
- Have **time** (patient attackers who can wait for misconfigurations)
- Attempt **supply chain attacks** (malicious MCP servers, malicious plugins, malicious checkpoint restore)

We **do not** assume:

- The attacker has physical access to the host
- The attacker has compromised Postgres directly (that's an infra concern)
- The LLM provider is malicious (that's a vendor trust concern)

---

## STRIDE + OWASP ASI mapping

Hecate's threat model combines **STRIDE** (Microsoft's classic six-category framework) with the **OWASP Top 10 for Agentic Applications (ASI01–ASI10, 2026)**. The two frameworks are complementary:

| STRIDE category | Hecate mitigation (L1 layer) | OWASP ASI cross-ref | Status (2026-08-22) |
|-----------------|-------------------------------|---------------------|---------------------|
| **S** Spoofing | JWT + API Key (3.7) + SSO/SCIM/OIDC/SAML/LDAP (10.2) + Signed Agent Cards (SS6) | **ASI01 Agent Identity & AuthN** | Mitigated P3 |
| **T** Tampering | Guardrail Chains (1.3.5i E3) + Content Scanning (5.13a) + PII Masking (9.1) | **ASI02 Tool Misuse / Tampering** | Mitigated P3 (5.13a + E3) |
| **R** Repudiation | Audit Trail + SIEM Pipeline (SS5) + ApprovalCallback durable HITL pairs (1.3.4) | **ASI03 Repudiation & Audit** | Mitigated P3 |
| **I** Information Disclosure | PII Anonymizer (Presidio + Regex, 9.1) + Outbound DLP EF1 (9.10) + Encryption Service | **ASI04 Info Disclosure** | DLP Engine P4 (per ADR-025); Presidio ✅ |
| **D** Denial of Service | Rate Limiter + Quota Enforcement + Circuit Breaker + Async Task Queue | **ASI05 Resource Exhaustion** | Mitigated P3 |
| **E** Elevation of Privilege | RBAC + Two-Tier Identity (11.17, P4) + Per-Token-Type Auth Pipeline (11.16, P4) + ApprovalCallback (1.3.4) | **ASI06 Privilege Escalation** | Partial P3; 11.16/11.17 deferred P4 |

**OWASP ASI-only threats** (no direct STRIDE equivalent) tracked under [ADR-026](../adr/026-security-shield-enhancement.md):

- **ASI07 Goal Drift Detection** — SS1 Agent Runtime Protection (P4)
- **ASI08 Rogue Agent Containment** — SS2 Automated Red Teaming (P4)
- **ASI09 Multi-Agent Trust Compromise** — SS6 Multi-Agent Trust (P5 per catalog; partially shipped via Signed Cards P3)
- **ASI10 Supply Chain / Tool Poisoning** — T0 Trust Gate (5.5 enh, shipped 2026-08-19) + Content Scanning (5.13a, shipped 2026-08-18) — **Mitigated P3**

See the L1 architecture diagram (`docs/design/hardware/hecate_architecture_l1.drawio` Security Shield swimlane) for the implementation view.

---

## STRIDE analysis

Hecate's threat model combines **STRIDE** (Microsoft's classic six-category framework) with the **OWASP Top 10 for Agentic Applications (ASI01–ASI10, 2026)**. The two frameworks are complementary:

| STRIDE category | Hecate mitigation (L1 layer) | OWASP ASI cross-ref | Status (2026-08-22) |
|-----------------|-------------------------------|---------------------|---------------------|
| **S** Spoofing | JWT + API Key (3.7) + SSO/SCIM/OIDC/SAML/LDAP (10.2) + Signed Agent Cards (SS6) | **ASI01 Agent Identity & AuthN** | Mitigated P3 |
| **T** Tampering | Guardrail Chains (1.3.5i E3) + Content Scanning (5.13a) + PII Masking (9.1) | **ASI02 Tool Misuse / Tampering** | Mitigated P3 (5.13a + E3) |
| **R** Repudiation | Audit Trail + SIEM Pipeline (SS5) + ApprovalCallback durable HITL pairs (1.3.4) | **ASI03 Repudiation & Audit** | Mitigated P3 |
| **I** Information Disclosure | PII Anonymizer (Presidio + Regex, 9.1) + Outbound DLP EF1 (9.10) + Encryption Service | **ASI04 Info Disclosure** | DLP Engine P4 (per ADR-025); Presidio ✅ |
| **D** Denial of Service | Rate Limiter + Quota Enforcement + Circuit Breaker + Async Task Queue | **ASI05 Resource Exhaustion** | Mitigated P3 |
| **E** Elevation of Privilege | RBAC + Two-Tier Identity (11.17, P4) + Per-Token-Type Auth Pipeline (11.16, P4) + ApprovalCallback (1.3.4) | **ASI06 Privilege Escalation** | Partial P3; 11.16/11.17 deferred P4 |

**OWASP ASI-only threats** (no direct STRIDE equivalent) tracked under [ADR-026](../adr/026-security-shield-enhancement.md):

- **ASI07 Goal Drift Detection** — SS1 Agent Runtime Protection (P4)
- **ASI08 Rogue Agent Containment** — SS2 Automated Red Teaming (P4)
- **ASI09 Multi-Agent Trust Compromise** — SS6 Multi-Agent Trust (P5 per catalog; partially shipped via Signed Cards P3)
- **ASI10 Supply Chain / Tool Poisoning** — T0 Trust Gate (5.5 enh, shipped 2026-08-19) + Content Scanning (5.13a, shipped 2026-08-18) — **Mitigated P3**

See the L1 architecture diagram (`docs/design/hardware/hecate_architecture_l1.drawio` Security Shield swimlane) for the implementation view.

### S — Spoofing

| Threat | Attack scenario | Current mitigation | Gap? |
|---|---|---|---|
| **API key forgery** | Attacker guesses or brute-forces an API key | `HECATE_API_KEYS` uses secure random tokens (default 32+ bytes) | None |
| **JWT forgery** | Attacker forges a JWT to impersonate a user | JWT signed with HS256 + secret loaded from secure storage | None |
| **Agent Card signature forgery** | Attacker creates a fake AgentCard pointing at their malicious agent | Hecate uses ES256 + JWS; signed cards include `kid` for key pinning ([A2A Architecture](a2a-architecture.md#trust-model)) | None |
| **MCP server identity spoofing** | Attacker claims to be GitHub MCP server | Hecate does NOT verify MCP server identity by default | **GAP** — tracked in our backlog |
| **SSO token replay** | Attacker captures an OIDC token and replays it | OIDC tokens have short TTL (~5 min); refresh tokens stored server-side | None |

**Critical gap**: MCP server identity verification. An agent calling `mcp://github.com/` has no way to verify the actual server it reaches is GitHub. **Mitigation today**: only use trusted MCP servers. Future fix: TLS certificate pinning or DNS-verified server identity (P3).

---

### T — Tampering

| Threat | Attack scenario | Current mitigation | Gap? |
|---|---|---|---|
| **Checkpoint / event log tampering** | Attacker with DB write access modifies execution state (event log or its materialized checkpoint caches) to influence future agent runs | Event log is the source of truth ([ADR-030](adr/030-event-sourced-execution-state.md)); `PostgresEventStore` hash chain; cache divergence fails closed (`PROJECTION.EQUIVALENT` invariant) and rebuilds by re-folding the log | Partial — detection is post-hoc |
| **Audit log tampering** | Attacker with DB write access deletes audit events | Hash chain in `audit_logs` (each event links to previous hash); SIEM forwarding | Partial — depends on SIEM ingestion latency |
| **Agent Card payload tampering** | Attacker modifies skill list or endpoint after signing | JWS signature covers full payload | None |
| **Plugin manifest tampering** | Attacker modifies a plugin package after admin signs it | Plugins run in same Python process; no isolation by default | **GAP** |
| **Knowledge base poisoning** | Attacker uploads documents to influence RAG | Guardrail hooks (`PIIAnonymizer`, `InjectionDetectionHook`) can flag suspicious content | Partial — depends on hook config |
| **Prompt injection via tool result** | A malicious MCP server returns text designed to override system prompt | Guardrail `PreLLMHook` can sanitize tool output | Partial |
| **Backup tampering** | Attacker with backup storage access modifies backups | SHA256 checksum verified before restore; immutable storage tier recommended | None |

**Critical gap** (partially mitigated by T0 trust gate): Plugin isolation. A malicious in-process `python:` plugin could previously affect the engine. **Mitigation today**: T0 trust gate per [ADR-029](adr/029-trust-tiered-kernel-plugin-architecture.md) (5.5 (enh) T0 Tightening) rejects `python:module:Class` entries whose module is not first-party (`hecate` / `hecate.*`) — SaaS rejects outright; self-hosted default-denies with `PLUGIN_PYTHON_ENTRY_ALLOWLIST`. Install-time pre-check rolls back the extracted directory on rejection. MCP server plugins (`mcp://`) connect through the MCP client; Agent Plugins 1.0 stdio entries run in the 9.4c container sandbox. Plugin `permissions` are still reviewable in the manifest. **Future fix**: out-of-process sandboxed plugin execution (P5).

---

### R — Repudiation

| Threat | Attack scenario | Current mitigation | Gap? |
|---|---|---|---|
| **Admin denies action** | Admin performs a sensitive op and denies it | All admin actions audited with `actor_id`, timestamp, IP | None |
| **User denies message** | User claims they didn't send a message | Per-user message audit with conversation ID | None |
| **Audit log deletion** | Attacker deletes audit events to cover tracks | Append-only hash chain; SIEM forwarding | Partial — depends on SIEM |
| **Workflow modification denial** | Developer claims they didn't modify a workflow | Workflow versions with author/timestamp | None |

---

### I — Information Disclosure

| Threat | Attack scenario | Current mitigation | Gap? |
|---|---|---|---|
| **Cross-tenant data leak** | Workspace A reads workspace B's data | All 38 tenant-scoped models filter by `workspace_id` ([Multi-Tenancy](multi-tenancy-architecture.md#tenant-isolation-count)); 3-layer defense | None |
| **PII in LLM context** | User sends PII; LLM provider sees it | PreLLM `PIIAnonymizer` hook replaces with tokens; `StreamDeanonymizer` restores for user | None |
| **PII in audit log** | Audit log contains raw PII | Audit hooks apply same PII anonymization before logging | None |
| **PII in backup** | Backup contains raw PII | Backups are full DB snapshots; encryption-at-rest via storage backend (S3 SSE / MinIO SSE) | None |
| **LLM provider sees secrets** | User query accidentally includes API key | PreLLM hook scrubs patterns matching `sk-`, `key-`, etc. | None |
| **LLM provider sees proprietary docs** | Knowledge base contains trade secrets | Hecate is self-hosted — LLM provider sees what you send; **deployer's responsibility** | N/A (deployer's call) |
| **Timing attacks on rate limits** | Attacker probes API to learn internal state | Constant-time rate limit checks | None |
| **A2A cross-org data leak** | Hecate A2A server accidentally exposes org B's data | A2A server uses bearer token; tokens are workspace-scoped | None |
| **MCP tool returns too much data** | MCP tool returns full DB row when only ID is needed | No automatic redaction; depends on tool implementation | **GAP** |
| **Backup access by ex-employee** | Ex-admin still has backup storage credentials | Storage credentials should be rotated on admin departure (manual) | Process gap |

**Critical gap**: MCP tool output redaction. Tools can return sensitive data that flows into LLM context. **Mitigation today**: trust your MCP server. Future fix: tool-output redaction plugin (P3).

---

### D — Denial of Service

| Threat | Attack scenario | Current mitigation | Gap? |
|---|---|---|---|
| **API rate exhaustion** | Attacker floods with requests | Per-workspace rate limits (default 60 req/min); `RATE_LIMIT_REQUESTS_PER_MINUTE` | None |
| **LLM cost exhaustion** | Agent loops burn through API budget | `Budget Governance` ([ADR-025](adr/025-enterprise-foundation-enhancement.md)); workspace daily cost cap | None |
| **Agent self-loop DoS** | Misconfigured agent loops infinitely | `max_iterations` in Pregel runtime; circuit breakers | None |
| **Event log / checkpoint fill-up** | Attacker or bug fills disk with event log or caches | Session TTL cleanup (completed 30d / interrupted 7d, cascade delete); retention policy; log truncation/archival is a tracked follow-up ([ADR-030](adr/030-event-sourced-execution-state.md)) | Process gap |
| **Knowledge base DoS** | Attacker uploads millions of tiny documents | Upload rate limit per workspace | Partial |
| **Audit log fill-up** | Audit grows unbounded | Archive to cold storage after 30 days | None |
| **Postgres connection pool exhaustion** | Many concurrent requests | asyncpg pool sized via `POSTGRES_POOL_SIZE` (default 20) | None |
| **MCP server DoS** | Slow MCP server ties up worker | Circuit breaker per remote agent ([A2A](a2a-architecture.md#connection-pool)); 5-failure threshold | None |
| **Token bucket exhaustion** | One workspace hogs shared LLM quota | Per-workspace rate limits + budget governance | None |

**Critical gap**: Knowledge base upload rate limit. **Mitigation today**: configure `RATE_LIMIT_REQUESTS_PER_MINUTE` conservatively. Future fix: per-KB quota (P3).

---

### E — Elevation of Privilege

| Threat | Attack scenario | Current mitigation | Gap? |
|---|---|---|---|
| **RBAC bypass** | User gains ADMIN role without authorization | `WorkspaceMemberModel.role` enforced at every endpoint; server-side checks | None |
| **Cross-workspace access** | Editor in workspace A accesses workspace B | All queries filter by `workspace_id` from auth context | None |
| **API key privilege escalation** | API key with VIEWER role mutates resources | API key `role` field enforced; same RBAC as user tokens | None |
| **Plugin privilege escalation** | Plugin declares `db:read` but writes | Permission system enforced at boundary ([Extension SPI](extension-architecture.md#permissions-model)) | None |
| **Prompt injection → code execution** | Malicious KB document tricks agent into executing code | `execute_code` tool requires sandbox + risk level HIGH | Partial — sandbox is required, but config-dependent |
| **WebSocket hijack → admin actions** | Attacker takes over WS session | Bearer auth on every WS message | None |
| **A2A → bypass auth** | External A2A agent calls Hecate without auth | A2A server enforces X-API-Key / Bearer | None |
| **CLI → direct DB access** | Attacker with shell access connects to Postgres directly | Operational concern (DB access control); Hecate provides audit but can't prevent | N/A (infra) |

---

## Attack tree

Most likely attack paths in priority order:

```
1. Steal LLM provider API key
   ├── 1a. Phishing admin (humans)
   ├── 1b. Dump .env from backup (target: backup storage)
   ├── 1c. Exploit LLM proxy (Hecate itself)
   └── 1d. Prompt injection to extract (low success)

2. Exfiltrate conversation data
   ├── 2a. Compromise workspace admin (insider)
   ├── 2b. RBAC bypass via direct DB access
   ├── 2c. Backup leak (target: backup storage)
   └── 2d. PII leakage via unredacted log

3. Modify agent behavior (poison)
   ├── 3a. KB poisoning (upload crafted documents)
   ├── 3b. Plugin supply chain attack
   ├── 3c. Prompt injection via MCP tool result
   └── 3d. Checkpoint restore from poisoned backup

4. Cost-based DoS
   ├── 4a. Agent loop infinite recursion
   ├── 4b. Workspace token budget exhaustion (legitimate but expensive)
   └── 4c. LLM provider rate limit hit (cascading failure)
```

---

## Mitigations already in place

Summary of security features Hecate ships today:

| Layer | Feature | Where |
|---|---|---|
| **AuthN** | API Key / JWT / OIDC / SAML / LDAP | [Multi-Tenancy](multi-tenancy-architecture.md#authentication-providers) |
| **AuthZ** | WorkspaceRole (ADMIN/EDITOR/VIEWER) | [Multi-Tenancy](multi-tenancy-architecture.md#rbac-matrix) |
| **Network** | TLS at reverse proxy | [Reference Architectures](reference-architectures.md#network-architecture) |
| **Input** | PII anonymization, injection detection | [Security Architecture](security-architecture.md) |
| **Output** | PII deanonymization, toxicity filtering | [Security Architecture](security-architecture.md) |
| **Audit** | Append-only hash chain + SIEM | [Observability](observability-architecture.md#audit-architecture) |
| **Detection** | BulkDelete / OffHours / UnusualIP rules | [Observability](observability-architecture.md#audit-detection-rules) |
| **Limits** | Per-workspace rate limit + budget | [Multi-Tenancy](multi-tenancy-architecture.md#quotas-and-rate-limits) |
| **Egress** | Plugin permission system | [Extension SPI](extension-architecture.md#permissions-model) |
| **Signing** | AgentCard ES256 + JWS + RFC 8785 | [A2A Architecture](a2a-architecture.md#trust-model) |
| **Backup** | SHA256 checksum + verification | [Backup & Recovery](backup-recovery-architecture.md#verification) |
| **Self-hosting** | Default (data never leaves your network) | [Positioning](positioning.md#hecate-vs-salesforce-agentforce) |

---

## Critical gaps

Gaps identified above (tracked in our backlog):

| Gap | Severity | Target phase |
|---|---|---|
| **MCP server identity verification** | High | P2 |
| **Plugin sandboxing** | High | P5 |
| **Tool-output redaction** | Medium | P3 |
| **KB upload rate limit** | Medium | P3 |
| **Per-tenant encryption keys** | Medium | P3 |
| **Multi-region active-active backup** | Low | P5 |
| **External security audit** | High (Beta exit criterion) | P1 (Beta) |

---

## Security testing recommendations

### What we test today

- Unit tests for auth flow, permission checks, isolation boundaries
- Integration tests for end-to-end API auth
- Penetration testing: **not yet scheduled** (Beta exit criterion)

### What you should test in your deployment

- **Quarterly**: external penetration test against your deployed instance
- **Per release**: verify env vars haven't regressed (config drift detection)
- **Monthly**: review audit logs for anomalies
- **Weekly**: backup restoration drill (per [Backup & Recovery](backup-recovery-architecture.md#disaster-recovery-drills))

---

## Reporting a vulnerability

If you discover a security issue in Hecate, please report privately (see `SECURITY.md` when published) — **do not** open a public GitHub issue.

We follow a **90-day coordinated disclosure** policy. See `SECURITY.md` (when published) at the repo root for the full procedure.

---

## Implementation references

- `src/hecate/auth/` — auth providers (JWT, OIDC, SAML, LDAP, API key)
- `src/hecate/services/audit/` — audit pipeline
- `src/hecate/services/observability/` — traces, metrics, logs
- `src/hecate/engine/guardrail.py` — Pre/Post LLM/Tool hooks (adapt as chain stages)
- `src/hecate/engine/middleware.py` — ordered waterfall chain kernel (E3; BLOCK short-circuit, SANITIZE pass-through, monotonic tightening)
- `src/hecate/engine/shell_analysis.py` — content-aware shell command decomposition for tool gating
- `src/hecate/engine/monotonic_denials.py` — per-session denial tracker (resurrection blocked at runtime)
- `src/hecate/services/security/approval.py` — fail-closed approval with durable APPROVAL_ASKED/DECIDED audit pair
- `src/hecate/services/security/guardrail_assembly.py` — production wiring for both execution paths
- `src/hecate/plugin/permission.py` — plugin permission enforcement
- `src/hecate/a2a/signing.py` — AgentCard JWS signing
- `src/hecate/services/backup/verification.py` — backup integrity verification
- `src/hecate/services/budget/` — budget governance

## Related documents

- [Security Architecture](security-architecture.md) — what Hecate ships
- [Security Hardening](../how-to/security-hardening.md) — operational checklist
- [Multi-Tenancy Architecture](multi-tenancy-architecture.md) — RBAC and isolation
- [Observability Architecture](observability-architecture.md) — audit + SIEM
- [Extension SPI & Plugin Architecture](extension-architecture.md) — plugin permissions
- [A2A Architecture](a2a-architecture.md) — AgentCard trust model
- [Backup & Recovery Architecture](backup-recovery-architecture.md) — backup integrity
- Security Architecture — current security posture