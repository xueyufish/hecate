# ADR-018: Zero Trust Identity Architecture for Enterprise Agent Access

> **Status**: Proposed
> **Date**: 2026-07-01

## Context

Hecate's current access control uses a single-tier identity model: an API Key or JWT identifies "who is calling," but does not distinguish between an application calling on behalf of users (server-to-server) and an end-user calling directly (user-to-server). This creates ambiguity in audit trails, access control granularity, and multi-tenant governance.

Enterprise platforms (Salesforce Agentforce, Palantir AIP, Google ADK, Dify) have converged on more sophisticated identity models:

- **Dify** separates App-level identity (API Key for application integration) from User-level identity (JWT for end-user sessions)
- **Salesforce** uses `bypassUser` flag to distinguish system-level operations from user-scoped operations
- **Google ADK** uses IAM-based service accounts with principle of least privilege
- **Palantir AIP** enforces unified governance: identity + data + API + AI trust policies at a single enforcement layer

The question is how to evolve Hecate's identity model to support enterprise-grade access control without breaking existing API consumers.

## Decision

Adopt a **Zero Trust Identity Architecture** with four interconnected components:

### 1. Two-Tier Identity Model (11.17)

Distinguish two identity tiers for every API request:

| Tier | Token Type | Scope | Use Case |
|------|-----------|-------|----------|
| **App-level** | API Key (`hcat_*`) | Application identity | Server-to-server integration, CI/CD pipelines |
| **User-level** | JWT (Bearer) | End-user identity | Interactive sessions, per-user audit |

Both tiers can be combined: an App-level API Key carries a User-level JWT in a header to represent "application X acting on behalf of user Y." This enables granular access control and dual audit trails.

### 2. Per-Token-Type Auth Pipeline (11.16)

Route authentication through separate pipelines based on token type:

```
Request → Token Type Detection → ┌─ JWT Pipeline ──→ Verify HS256 + Expiry + RBAC
                                  ├─ APIKey Pipeline → Verify SHA-256 + Rate Limit
                                  ├─ PAT Pipeline ───→ Verify Scope + Rotation
                                  └─ OAuth SSO ──────→ Verify OIDC + Scope Mapping
```

Each pipeline has distinct verification steps, rate limits, and edition gating (Community vs Enterprise). The gateway-level router determines the pipeline before the request reaches business logic.

### 3. Platform-Level Governance (11.19)

A unified governance layer enforces 20+ policies across identity, data, API, and AI trust:

- **Identity policies**: Auto API key rotation, JWT auth enforcement, suspicious login detection
- **Data policies**: Real-time sensitive content blocking, PII masking, data residency rules
- **API policies**: Rate limiting, quota enforcement, model routing, fallback chains
- **AI trust policies**: Prompt injection defense, output toxicity filtering, hallucination detection

All policies are evaluated at the gateway before the request reaches the Agent Engine, providing a single choke point for security enforcement.

### 4. Zero Trust Principles (11.20)

Adopt Zero Trust architecture principles for agent-to-service communication:

- **IAM-based service accounts**: Each agent has a unique identity with scoped permissions (principle of least privilege)
- **Token exchange**: Use OAuth 2.0 Token Exchange (RFC 8693) for identity propagation across service boundaries
- **Per-agent identity**: Agents carry cryptographic identity when invoking tools or communicating with other agents (A2A)
- **Continuous verification**: Every tool call, LLM invocation, and knowledge query is authenticated and authorized — no implicit trust based on network position

## Architecture

```
                    ┌─────────────────────────────────────┐
                    │        Platform-Level Governance     │
                    │  (20+ policies: identity + data +   │
                    │   API + AI trust)                    │
                    └────────────────┬────────────────────┘
                                     │
    ┌────────────────────────────────┼────────────────────────────────┐
    │                                │                                │
    ▼                                ▼                                ▼
┌───────────┐              ┌──────────────┐              ┌──────────────────┐
│ App-Level │              │  User-Level   │              │  Agent Identity  │
│ (API Key) │              │  (JWT)        │              │  (Service Acct)  │
│           │              │               │              │                  │
│ Server-   │              │ Interactive   │              │ Per-agent scoped │
│ to-server │              │ sessions      │              │ permissions      │
└─────┬─────┘              └──────┬───────┘              └────────┬─────────┘
      │                           │                               │
      └───────────┬───────────────┘                               │
                  │                                               │
                  ▼                                               ▼
          ┌──────────────┐                              ┌──────────────────┐
          │ Token Type    │                              │ OAuth 2.0 Token  │
          │ Router        │                              │ Exchange (RFC    │
          │ (4 pipelines) │                              │ 8693)            │
          └──────────────┘                              └──────────────────┘
```

## Rationale

- **Two-Tier Identity**: App-level identity answers "which application?" and User-level identity answers "which end-user?" — both are needed for enterprise audit trails. Without this distinction, all API Key traffic is attributed to the application, losing per-user accountability.

- **Per-Token-Type Pipeline**: Different token types have fundamentally different verification requirements (JWT needs signature + expiry; API Key needs hash + rate limit; OAuth SSO needs OIDC discovery + scope mapping). A single unified pipeline creates complexity; separate pipelines are simpler to reason about and extend.

- **Platform-Level Governance**: Consolidating identity + data + API + AI trust policies at a single enforcement layer (inspired by Salesforce MuleSoft Flex Gateway and Palantir Trust Layer) avoids policy scattered across middleware, engine, and services.

- **Zero Trust**: Traditional perimeter-based security assumes internal traffic is trusted. In agent systems, tools call external services, agents communicate with other agents (A2A), and LLM outputs can be manipulated. Zero Trust — verify every request regardless of source — is the only safe default.

## Consequences

- Feature 11.16 (Per-Token-Type Auth Pipeline) must be implemented before 11.17 (Two-Tier Identity)
- Feature 11.19 (Platform-Level Governance) requires a new gateway middleware layer
- Feature 11.20 (Zero Trust) requires per-agent identity management and OAuth 2.0 Token Exchange support
- Existing API Key consumers are not affected — the App-level tier is backward-compatible
- JWT consumers gain optional User-level audit capabilities when combined with App-level identity
- The A2A protocol (ADR-011) Signed Agent Cards become a natural extension of per-agent identity
