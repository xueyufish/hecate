# ADR-027: Ecosystem Enhancement Architecture

> **Status**: Proposed
> **Date**: 2026-07-02

## Context

Hecate's Ecosystem layer provides MCP bidirectional support, A2A protocol, webhook/event integration, OpenAI-compatible API, and planned features for plugin system, asset marketplace, industry packs, multi-channel access, SDK distribution, and i18n. Competitive analysis against Salesforce AgentExchange ($800M ARR), IBM Agent Catalog/Agent Connect, Google ARD specification, Huawei AI Model Partner Program, and Hugging Face agent ecosystem revealed 6 gaps:

| Gap | Description | Type | Priority |
|-----|-------------|------|----------|
| EC1 | **Agentic Resource Discovery (ARD)** — `ai-catalog.json` catalog format, federated registry, runtime discovery | New Feature | P5 (14.1) |
| EC2 | **Partner Monetization Infrastructure** — Stripe, revenue sharing, unified billing, auto-provisioning | New Feature | P5 (12.5) |
| EC3 | **Semantic Marketplace Discovery** — vector search by intent | 12.0 Enhancement | P5 |
| EC4 | **Community Agent Gallery** — `agent.json` format, one-click install/fork | 13.14 Enhancement | P5 |
| EC5 | **Cross-Surface Experience Layer** — define once, deploy everywhere (AXL pattern) | 11.13 Enhancement | P5 |
| EC6 | **Governed Agent Catalog** — approval workflow, any framework, cross-cloud | 12.0 Enhancement | P5 |

## Decision

### 1. Agentic Resource Discovery (EC1/14.1) — Open Standard Compliance

Implement ARD as a **publishing + discovery layer** on top of existing A2A/MCP infrastructure:

```
Hecate Instance
    │
    ├── ai-catalog.json (published on domain)
    │   ├── agents: [ {name, description, capabilities, a2a_endpoint, trust_metadata} ]
    │   ├── skills: [ {name, description, install_url} ]
    │   ├── mcp_servers: [ {name, description, transport, endpoint} ]
    │   └── tools: [ {name, description, api_schema} ]
    │
    ├── ARD Crawler (inbound — indexes external catalogs)
    │   └── Discovers capabilities from Google Agent Registry, HF Discover, other ARD endpoints
    │
    └── Runtime Discovery API (outbound — agents query at runtime)
        └── POST /api/v1/discover {intent: "I need to transcribe audio"}
            → Returns matched capabilities with publisher verification
```

**Design principle**: ARD complements (not replaces) A2A AgentCard. AgentCard is per-agent discovery; ARD is catalog-level discovery across all capability types.

### 2. Partner Monetization Infrastructure (EC2/12.5) — Commercial Pipeline

```
Partner (ISV)
    │
    ▼ (1) Register as Partner
┌──────────────────────────────────────────┐
│  Partner GTM Console                      │
│  ├── Product Management (create/edit)    │
│  ├── Offer Creation (pricing, terms)     │
│  ├── Invoice Tracking                    │
│  └── Payout Dashboard                    │
└───────────────────┬──────────────────────┘
                    │
                    ▼ (2) Customer purchases in Marketplace
┌──────────────────────────────────────────┐
│  Stripe Payment Processing                │
│  ├── Credit card / ACH                   │
│  ├── Automated invoicing                 │
│  └── Unified billing (all assets in 1 bill)│
└───────────────────┬──────────────────────┘
                    │
                    ▼ (3) Revenue split
┌──────────────────────────────────────────┐
│  Revenue Sharing Engine                   │
│  ├── Configurable split (e.g., 70/30)   │
│  ├── Automatic calculation per transaction│
│  └── Partner payout via Stripe Connect   │
└───────────────────┬──────────────────────┘
                    │
                    ▼ (4) Instant activation
┌──────────────────────────────────────────┐
│  Auto-Provisioning                         │
│  ├── License generation                   │
│  ├── Asset activation in customer workspace│
│  └── Configuration auto-setup            │
└──────────────────────────────────────────┘
```

### 3-6. Enhancements

**EC3 (Semantic Discovery)**: Embed marketplace listings as vectors using BGE-M3. Query-time: embed user intent → cosine similarity → ranked results. Failed searches with >3 retries become bounty signals.

**EC4 (Community Agent Gallery)**: `agent.json` format (name, system_prompt, tools, knowledge_bases, examples). One-click install creates agent in workspace. Fork creates editable copy. Trace Gallery shares anonymized execution traces. Harness Registry lists compatible runtimes.

**EC5 (Cross-Surface Experience Layer)**: SurfaceAdapterABC — `render(agent_response, surface_type) → SurfaceComponent`. Surface types: web (rich HTML), mobile (adaptive cards), Slack (blocks), Teams (adaptive cards), voice (SSML), CLI (formatted text). Define agent behavior once; surface adapters handle rendering per channel.

**EC6 (Governed Catalog)**: Listing lifecycle: draft → submitted → security_scan → evaluation → approved → published → deprecated. Security scan reuses Plugin Security & Signing (5.13). Evaluation runs agent against test suite. Any framework supported via standardized manifest.

## Architecture Diagram

```
         ┌─────────────────────────────────────────────────┐
         │          Agentic Web (Federated Registries)      │
         │  Google Agent Registry · HF Discover · Others    │
         └────────────────────┬────────────────────────────┘
                              │ ARD Protocol
                    ┌─────────▼─────────┐
                    │  Hecate ARD Layer  │
                    │  (EC1/14.1)        │
                    │  ai-catalog.json   │
                    │  Crawler + API     │
                    └─────────┬─────────┘
                              │
    ┌─────────────────────────┼─────────────────────────┐
    │                         │                         │
    ▼                         ▼                         ▼
┌──────────┐         ┌───────────────┐         ┌──────────────┐
│ Marketplace│         │ Partner GTM   │         │ Community    │
│ (12.0)    │         │ Console (EC2) │         │ Gallery (EC4)│
│           │         │               │         │              │
│ Semantic  │         │ Stripe +      │         │ agent.json   │
│ Search    │         │ Revenue Share │         │ Trace Gallery│
│ (EC3)     │         │ Auto-Provision│         │ Harness Reg  │
│           │         │               │         │              │
│ Governed  │         │ Payouts       │         │ Fork/Install │
│ Catalog   │         │               │         │              │
│ (EC6)     │         │               │         │              │
└──────────┘         └───────────────┘         └──────────────┘
    │
    ▼
┌──────────────────────────────────────────────────┐
│  Cross-Surface Experience Layer (EC5/11.13)       │
│  Define Once → Deploy to:                         │
│  Web · Mobile · Slack · Teams · Voice · CLI       │
│  SurfaceAdapterABC renders per channel            │
└──────────────────────────────────────────────────┘
```

## Consequences

- **Positive**: ARD compliance makes Hecate discoverable in the agentic web; partner monetization enables commercial ecosystem; cross-surface layer eliminates per-channel rebuild
- **Negative**: ARD crawler adds infrastructure dependency; Stripe integration requires PCI compliance; cross-surface layer adds rendering abstraction overhead

## Related Documents

- [Ecosystem Design](../ecosystem-design.md) — Detailed design for EC1-EC6
- [ADR-011: A2A Protocol Adoption](011-a2a-protocol-adoption.md) — A2A foundation for ARD
- [ADR-016: Platform SPI Architecture](016-platform-spi-architecture.md) — Plugin SPI foundation
- [ARD Specification](https://ard-spec.org/) — Open standard reference
