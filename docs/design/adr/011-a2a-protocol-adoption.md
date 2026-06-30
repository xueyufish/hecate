# ADR-011: A2A Protocol Adoption for Cross-Framework Agent Interoperability

> **Status**: Accepted
> **Date**: 2026-06-29

## Context

Hecate's multi-agent orchestration (6 patterns: Hierarchical, Handoff, Pipeline, Broadcast, Negotiation, Debate) is internal-only — agents within the same Hecate instance can collaborate, but cannot communicate with agents from other frameworks (LangGraph, CrewAI, ADK, Salesforce Agentforce). In 2025-2026, the industry converged on the A2A (Agent-to-Agent) protocol as the standard for cross-framework agent communication.

A2A was launched by Google in April 2025 with 50+ enterprise partners, donated to the Linux Foundation in June 2025, and reached v1.0 in early 2026. By mid-2026, 150+ organizations (AWS, Microsoft, Salesforce, SAP, IBM, ServiceNow) have adopted it in production. The protocol standardizes agent discovery (Agent Cards), task lifecycle (submitted→working→completed/failed), and artifact exchange.

## Decision

Adopt A2A protocol as a first-class integration protocol alongside MCP. Implement:

1. **A2A Server** — Expose Hecate agents as A2A-compliant services with Agent Card discovery (`/.well-known/agent.json`)
2. **A2A Client** — Consume external A2A agents as remote sub-agents via `RemoteA2aAgent` abstraction
3. **Signed Agent Cards** — Cryptographic signatures on Agent Cards for identity verification (A2A v1.0 security feature)
4. **Task Lifecycle** — Implement the A2A task state machine (submitted→working→completed/cancelled/failed)

## Rationale

- **Industry convergence**: 150+ organizations, Linux Foundation governance, production-ready SDKs in Python/JS/Java/Go/.NET
- **Complementary to MCP**: A2A handles agent-to-agent coordination (horizontal), MCP handles agent-to-tool access (vertical). The two protocols compose cleanly.
- **Cross-framework interop**: Hecate agents can be consumed by any A2A-compliant platform (Salesforce, SAP, ServiceNow) without custom integration
- **Future-proof**: A2A v1.0 includes signed Agent Cards, multi-tenancy, gRPC support, and W3C Trace Context propagation

## Consequences

- Hecate agents become discoverable and callable by external platforms
- External agents can be used as sub-agents in Hecate workflows
- The Engine layer needs an A2A integration point (alongside EnginePort)
- Agent Card management (creation, signing, rotation) becomes a new operational concern
- Security model expands: signed cards, OAuth2 scopes, mTLS for agent-to-agent auth
