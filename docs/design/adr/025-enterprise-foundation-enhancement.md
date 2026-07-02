# ADR-025: Enterprise Foundation Enhancement Architecture

> **Status**: Proposed
> **Date**: 2026-07-02

## Context

Hecate's Enterprise Foundation delivers multi-tenancy (Org → Workspace → User), RBAC, JWT/API Key auth, Docker sandbox, PII masking, full-chain tracing, cost dashboard, audit logs, and the Ops Center. Competitive analysis against Salesforce Agentforce Trust Layer, HashiCorp Vault AI Agent support, Palantir Foundry lineage, and the 2026 AI Agent DLP product category (Control Zero, DAT, Pipelock, ORION, Zedly Shield) revealed 6 gaps:

| Gap | Description | Type | Priority |
|-----|-------------|------|----------|
| EF1 | **Outbound DLP Engine** — scan outbound LLM requests and tool outputs for sensitive data exfiltration | New Feature | P4 (9.10) |
| EF2 | **Enterprise Vault Integration** — HashiCorp Vault, AWS Secrets Manager, Azure Key Vault | New Feature | P4 (10.8) |
| EF3 | **Data Lineage Pipeline** — extend Decision Lineage to full RAG data provenance | 6.21 Enhancement | P4 |
| EF4 | **Multi-Region Data Sovereignty** — region-pinned deployment, GDPR Article 44 compliance | 13.6 Enhancement | P4 |
| EF5 | **Zero Data Retention Policy** — provider-level retention enforcement | 6.8 Enhancement | P4 |
| EF6 | **Confidential Computing Mode** — HYOK, data capsules, air-gapped inference | 13.16 Enhancement | P5 |

These gaps span four layers:
1. **Data exfiltration prevention** — Outbound DLP is a new 2026 product category that traditional DLP doesn't cover
2. **Secret management** — Enterprise vaults with dynamic secrets are table stakes for enterprise deployment
3. **Data governance** — Lineage, sovereignty, and retention controls for compliance
4. **Confidential computing** — Air-gapped and HYOK for defense/healthcare/financial

## Decision

### 1. Outbound DLP Engine (EF1/9.10) — Multi-Point Exfiltration Prevention

Build outbound DLP as a **pipeline interceptor** at three critical data flow points:

```
Agent Execution Pipeline
    │
    ▼
┌──────────────────────────────────────────────────┐
│  Scan Point 1: Pre-LLM                            │
│  Intercept outbound request to LLM provider       │
│  Scan: prompt text + context window content       │
│  Patterns: API keys, PII, source code, secrets    │
│  Action: Redact (replace with token) or Block     │
└──────────────────────────────────────────────────┘
    │ (sanitized request forwarded to provider)
    ▼
┌──────────────────────────────────────────────────┐
│  Scan Point 2: Post-Tool                          │
│  Intercept tool execution results                 │
│  Scan: tool output before entering context        │
│  Patterns: database dumps, file contents, API     │
│  responses containing sensitive data              │
│  Action: Redact or Block                          │
└──────────────────────────────────────────────────┘
    │ (sanitized result enters context)
    ▼
┌──────────────────────────────────────────────────┐
│  Scan Point 3: Pre-Memory                         │
│  Intercept data before persistence                │
│  Scan: memory store, vector DB embeddings         │
│  Prevents: PII entering long-term storage         │
│  Action: Redact or Block                          │
└──────────────────────────────────────────────────┘
```

**Pattern library** (extensible):
- API keys: AWS (`AKIA...`), Google (`AIza...`), Azure, Stripe, GitHub — with format validation
- PII: SSN (with area-group-serial validation), credit card (Luhn checksum), passport, phone
- Source code: language-specific fingerprints (Python `def`/`class`, Java `public class`)
- Custom: org-specific patterns (internal project codes, employee IDs)

**Cross-request entropy tracking**: Maintains a sliding window of outbound payloads per session. Detects secrets split across multiple requests (slow-drip exfiltration). Flags when accumulated entropy exceeds threshold.

**Design principle**: DLP is a **PostToolHook + PreLLMHook interceptor**. It plugs into the existing Guardrail Hook system (ADR-008) — no new execution path needed.

### 2. Enterprise Vault Integration (EF2/10.8) — Dynamic Secrets

Replace static Fernet encryption with a **SecretProviderABC** abstraction supporting multiple backends:

```python
class SecretProviderABC(ABC):
    @abstractmethod
    async def get_secret(self, key: str, agent_id: str) -> str:
        """Retrieve a secret with agent-scoped access."""
        ...

    @abstractmethod
    async def put_secret(self, key: str, value: str, agent_id: str) -> None:
        """Store a secret with agent-scoped access."""
        ...

    @abstractmethod
    async def rotate_secret(self, key: str) -> str:
        """Trigger rotation and return new value."""
        ...
```

**Implementations**:
- `BuiltinSecretProvider` (existing Fernet — default for dev/small deployments)
- `VaultSecretProvider` (HashiCorp Vault with Agent Registry + OAuth resource server)
- `AWSSecretProvider` (AWS Secrets Manager with Lambda rotation)
- `AzureSecretProvider` (Azure Key Vault with managed identity)

**Vault integration** follows HashiCorp's 2026 AI agent pattern:
1. Agent authenticates via OAuth 2.0 token exchange (RFC 8693)
2. Vault validates JWT, resolves to Vault Identity entity
3. Vault checks Agent Registry enrollment
4. Vault issues dynamic, short-lived credential with scoped policy
5. Every access logged with `X-Correlation-ID` for audit attribution

**Design principle**: Agents never see static credentials. Every secret is dynamic, scoped, time-limited, and audit-logged. Secret rotation is transparent — agents auto-refresh on cache expiry.

### 3. Data Lineage Pipeline (EF3/6.21 Enhancement) — RAG Provenance

Extend Decision Lineage to track **full data transformation chain**:

```
Source Document (hash, version, upload_time)
    │
    ▼
Parser Output (format, pages, metadata extracted)
    │
    ▼
Chunker Output (chunk_id, offset, chunk_text_hash)
    │
    ▼
Embedding (model, dimension, vector_hash)
    │
    ▼
Vector Store (collection, point_id, stored_at)
    │
    ▼
Retrieval (query, score, rank, retrieved_at)
    │
    ▼
Agent Response (response_text, citations[])
    │
    ▼
Decision Lineage (who, what, when, data_version)
```

Each step records: input hash → transformation params → output hash. This creates an immutable provenance chain. Compliance queries like "Where did this answer come from?" trace from response → retrieval → vector store → chunk → source document.

### 4. Multi-Region Data Sovereignty (EF4/13.6 Enhancement) — Architectural Guarantees

**Region configuration** at deployment level:
```yaml
regions:
  eu-west:
    database: postgresql+asyncpg://eu-db.internal:5432/hecate
    vector_store: qdrant://eu-qdrant.internal:6333
    log_stream: eu-elasticsearch.internal
    llm_providers: [openai-eu, azure-eu, mistral-eu]
    data_residency: strict  # no cross-region data movement
  us-east:
    database: postgresql+asyncpg://us-db.internal:5432/hecate
    vector_store: qdrant://us-qdrant.internal:6333
    llm_providers: [openai-us, anthropic-us]
    data_residency: strict
```

**Cross-region policy gate**: Any data movement between regions requires explicit `CrossRegionTransferPolicy` approval. The gate blocks by default; administrators whitelist specific transfer scenarios.

### 5. Zero Data Retention Policy (EF5/6.8 Enhancement) — Provider Controls

**Provider registration** declares retention policy:
```python
class ProviderRetentionPolicy:
    provider_name: str           # e.g., "openai"
    retention_mode: str          # "zero" | "limited" | "full"
    retention_period_hours: int  # 0 for zero, N for limited
    training_allowed: bool       # Can provider train on this data?
    audit_logging: bool          # Log all requests to this provider
```

**Enforcement**: When an agent routes to a provider, the platform checks the workspace's data classification. If classification requires zero retention and the provider doesn't support it, the request is blocked with an audit entry.

### 6. Confidential Computing Mode (EF6/13.16 Enhancement) — Air-Gapped

**Three trust tiers**:
1. **Standard** (default): External LLM providers, standard encryption
2. **Confidential** (HYOK): Customer-managed encryption keys, confidential inference via supported providers
3. **Air-Gapped**: `AIRGAPPED=true`, zero outbound calls, Ollama sidecar for local inference, SQLite + local embedding

**Air-gapped deployment**:
```yaml
airgapped: true
llm:
  provider: ollama
  endpoint: http://localhost:11434
  model: llama3.3:70b
embedding:
  provider: local
  model: bge-m3
database: sqlite+aiosqlite:///hecate.db
vector_store: chroma  # in-process
```

## Architecture Diagram

```
                    ┌─────────────────────────────────────────┐
                    │          Agent Engine                    │
                    └──────────────┬──────────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────────┐
                    │          Security Pipeline               │
                    │  ┌────────────────────────────────────┐ │
                    │  │ Guardrail Hooks (existing)          │ │
                    │  │ + Outbound DLP (EF1)                │ │
                    │  │   Pre-LLM / Post-Tool / Pre-Memory  │ │
                    │  └────────────────────────────────────┘ │
                    └──────────────┬──────────────────────────┘
                                   │
           ┌───────────────────────┼───────────────────────┐
           │                       │                       │
    ┌──────▼──────┐       ┌───────▼───────┐       ┌──────▼──────┐
    │ Secret Mgmt │       │  Data Gov     │       │ Deployment  │
    │             │       │               │       │             │
    │ Vault (EF2) │       │ Lineage (EF3) │       │ Multi-Region│
    │ AWS SM      │       │ Sovereignty   │       │ (EF4)       │
    │ Azure KV    │       │ (EF4)         │       │ Air-Gapped  │
    │ Builtin     │       │ Zero Retain   │       │ (EF6)       │
    │ (Fernet)    │       │ (EF5)         │       │             │
    └─────────────┘       └───────────────┘       └─────────────┘
```

## Consequences

### Positive

- **Exfiltration prevention**: Outbound DLP closes the biggest enterprise security gap — agents processing sensitive data at machine speed with no egress control
- **Enterprise secret management**: Vault integration enables dynamic, audited, auto-rotating credentials — eliminating static key risks
- **Compliance-ready**: Data lineage + multi-region sovereignty + zero retention cover GDPR, HIPAA, EU AI Act, and enterprise audit requirements
- **Air-gapped deployment**: Opens defense, healthcare, and financial markets where no outbound data flow is acceptable

### Negative

- **DLP performance overhead**: Pattern matching on every outbound request adds latency (~5-15ms per scan). Mitigated by pattern caching and short-circuit evaluation
- **Vault dependency complexity**: Dynamic secrets require vault availability — if vault is down, agents can't authenticate. Mitigated by local secret caching with bounded TTL
- **Multi-region operational cost**: Each region needs independent infrastructure. Mitigated by making multi-region opt-in (single-region is default)

## Related Documents

- [Enterprise Foundation Design](../enterprise-foundation-design.md) — Detailed design for EF1-EF6 with personas and API endpoints
- [ADR-008: Security via Hooks](008-security-via-hooks.md) — Guardrail Hooks foundation for DLP integration
- [ADR-018: Zero Trust Identity Architecture](018-zero-trust-identity-architecture.md) — Identity and auth foundation
- [ADR-021: Ops Center Architecture](021-ops-center-architecture.md) — Composition pattern for compliance and audit
- [ADR-022: Model Hub Enhancement](022-model-hub-enhancement.md) — Parallel enhancement pattern
- [ADR-023: Tool Platform Enhancement](023-tool-platform-enhancement.md) — Parallel enhancement pattern
- [ADR-024: Knowledge & Memory Enhancement](024-knowledge-memory-enhancement.md) — Parallel enhancement pattern
