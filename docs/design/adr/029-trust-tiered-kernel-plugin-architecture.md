# ADR-029: Trust-Tiered Kernel and Plugin Architecture

## Status

Accepted (2026-08-14; supersedes nothing, extends ADR-002 five-layer architecture, ADR-008 security-via-hooks, ADR-016 Platform SPI, and the 5.5 Plugin System scope)

> **Update (5.13a)**: the scanning half of Rule 1's "elevation requires scanning + signing" is now feature **5.13a (Plugin Content Scanning, P3)** — install-time content scanning with fail-closed verdicts gates the 5.5c Agent Plugins ingest pipeline (go-live switch defaults on); see `openspec/specs/plugin-content-scanning/`. Signing remains 5.13 (P5, marketplace-bound).

## Context

Hecate's 2026-08-14 architecture re-scope (per `docs/research/2026-08-competitor-analysis.md` and the dsh source analysis) committed to event-sourced state (1.3.19), waterfall middleware (1.3.5i E3), and fail-closed approval — an architecture whose event log naturally acts as a bus that external capabilities can consume. This raises a structural question already answered differently across the industry:

- **dsh** (single-process local harness, one trust domain) makes *everything* a plugin — including the agent loop itself — composed in-process via Cordis at boot. This works because a local harness has one user, one trust boundary, and plugins chosen by that user.
- **Multi-tenant platforms** (Google Gemini Enterprise Agent Platform, Bedrock AgentCore, OpenClaw) converge on the opposite shape: a small hard kernel (runtime, identity, gateway, registry) plus isolated, operator-curated extensions and untrusted market content.

Hecate is a multi-tenant server platform: 35 data models carry `workspace_id`; plugin code loaded in-process would execute with access to every tenant's data, the event log, and the kernel itself. The ClawHub supply-chain findings (Snyk: 36% of sampled community skills contain prompt-injection patterns; 60+ CVEs) demonstrate that unisolated ecosystem code is a liability, not a feature.

Meanwhile Hecate already possesses most of the mechanical ingredients of a plugin platform — 11 engine ABCs, 4 SPIs, TP5's 8 plugin types, the sandbox pool (9.4c/9.4d), MCP client/registry (5.4b/5.4c), and declarative skill loading (5.9) — but lacks an explicit **trust model**: which capabilities may ever be third-party code, where that code runs, and who may install it.

## Decision

Hecate adopts a **minimal kernel + four mounting planes + five isolation tiers** architecture:

> The kernel retains only: the execution graph, the event log (source of truth), tenant isolation, and trust decision points. Everything else attaches as a plugin to one of four versioned planes. A plugin's isolation tier is determined by **the trust level of its installer**, not by its plugin type. First-party default implementations obey the same plugin contracts as third parties (dogfood discipline): batteries included, but replaceable.

### 1. Kernel boundary list

Module classifications (K = kernel, never pluggable; K-C = contract in kernel, implementations replaceable; P = platform plugin, isolated, admin-installed; E = ecosystem content, market-distributed, untrusted):

| Domain | Classification | Ruling |
|---|---|---|
| PregelRuntime, compiler, channels | **K** | Differentiation core |
| Event-sourced state + runtime invariants (1.3.19 + dsh-invariants-style checker) | **K** | Trust root |
| Engine ABCs (scheduler, eviction, checkpoint backends, ContextEngine, RetryStrategy…) | **K-C** | Interface is a kernel promise; defaults built behind SPI |
| Waterfall middleware chain (E3) | **K-C** | **The chain mechanism is kernel** — ordering, short-circuit, monotonic-denial and fail-closed semantics are safety invariants no plugin may alter; plugins contribute stages, never chain semantics |
| AuthN, RBAC decision points, workspace_id enforcement | **K** | Trust root |
| Tool-gating decision point, approval state machine (fail-closed), monotonic denial | **K** | Enforcement is never pluggable |
| Policy *engines* (Cedar / OPA / Dogwood adapters, 9.16) | **P** | Decision points in kernel, decision logic external |
| Sandbox executor routing | **K**; sandbox backends (gVisor/Kata/Firecracker/WASM 6.41) **P** | |
| Model access | **P** (ModelPluginBase) for non-standard protocols; LiteLLM-compatible endpoints remain **configuration, not plugins** | Avoid pluginizing what is data |
| Channels (ChannelBase) | **P** | |
| Tools | **P** (MCP default / container sandbox) | |
| Evaluation executors | **P/E** (EvaluatorBase; external via LangSmith/Braintrust per OE9 freeze) | |
| Observability | Kernel **emits only**; exporters/panels **P/E** | |
| Canvas/Studio authoring surface | **K, permanently** | No successful third-party-replaceable canvas exists industry-wide; the canvas is a permission-input surface (phishing risk) and owns the DSL round-trip guarantees |
| Node packs (declarative: schema.json + palette.json + binding.yaml; bind to existing node types / MCP tools / subgraph templates only) | **E / T4** | The open extension seam for the canvas; 1.1.24/1.1.25 are the first templates. No new execution semantics |
| Ops Center dashboards | **K with default**; declarative panels **E** (O8 direction) | |
| Skills, prompts, templates, agent definitions | **E** | Content, not code |
| Marketplace (12.0) | **K-C** | Kernel service; goods are E |

Security aphorism governing the whole table: **decision points in kernel, decision engines pluggable, enforcement never pluggable.**

### 2. Four mounting planes

Plugins do not load into the process; they attach to planes:

1. **Event plane (read)** — EventStore (1.3.19) consumers: observability exporters, evaluation samplers, audit, projections (8.21), marketplace stats.
2. **Interception plane (write)** — waterfall stages (E3): DLP, approval, compaction, injection detection.
3. **Execution plane** — MCP gateway (5.4a/c, migrating to spec 2026-07-28) + sandbox: tool and model plugins.
4. **Control plane** — REST API + declarative UI: skills, panels, node packs.

Each plane carries one versioned contract (event schema semver; waterfall stage API; MCP spec; declarative schemas). "Everything is a plugin" means "everything attaches to one of four planes" — not "everything loads into my process" (the dsh reading, which presumes a single trust domain Hecate does not have).

### 3. Five isolation tiers

| Tier | Mechanism | Boundary | Default for |
|---|---|---|---|
| **T0** in-process | importlib, same address space | none | **Only**: (a) first-party in-repo code; (b) engine ABC defaults & hot-path processors behind SPIs; (c) deployer's own source-tree extensions (fork / in-repo, deployed via the deployer's own CI). **Any artifact acquired by installation at runtime — regardless of signature — is never T0.** No exceptions at this time (see Deferred decisions) |
| **T1** containerized subprocess | Docker via sandbox pool (9.4c/9.4d), gRPC | fs/net-limited | platform plugins needing local execution; policy sidecars (OPA pattern) |
| **T2** external MCP service | protocol boundary; stateless under spec 2026-07-28 | network | **default for third-party tools**; external evaluators; tenant-private tools registered workspace-level (5.4c already supports isolated pools) |
| **T3** WASM (6.41) | deny-by-default capabilities | strongest, <1ms | hot-path computation: filters, scorers, pure-expression policies |
| **T4** declarative | pure data (SKILL.md, config_schema, panel JSON, node-pack schema) | no code escape (injection surface remains) | **default for everything market-distributed** |

Two governing rules:

- **Rule 1 — trust tier determines isolation tier; plugin type only determines contract.** The same tool plugin runs T1 when first-party-signed and T2 when community-sourced. Isolation is an *install-time* attribute. Market goods start at T4; elevation requires scanning + signing (5.13, P5).
- **Rule 2 — T0 is never granted to out-of-repo artifacts; first-party defaults should mostly avoid it too.** Dogfood discipline: first-party channels, evaluators, and panels build themselves against T1/T2 contracts.

### 4. Installation authority

- Self-hosted (any workspace count): P-tier installation belongs to the **org admin** — the party that owns the blast radius of the deployment. (An org admin blocked from P can fork anyway; blocking is security theater.)
- SaaS multi-tenant (post-13.1): P-tier installation belongs to **platform operators only**, matching Bedrock/Google operator-curated ecosystems. Tenant self-service extension is satisfied by **E-tier content plus T2 workspace-level MCP registration** — tenants never need to install code into the platform to extend it.
- RBAC mapping (extends existing 10.2 + 5.5's install/enable split): `plugin:install:platform` (P goods, T1-T3, install-time trust mark) → org admin (self-hosted) / operator (SaaS); `plugin:install:workspace` (E goods, T4 declarative) → workspace admin.

### 5. Canvas seam

Canvas authoring = K, permanently. Node packs = the open seam (E/T4). Third-party full studios: **technically possible** via the event plane + public REST API, but **not contractually supported** (no compat promises); revisit only with real customer demand. "Possible" and "supported" are explicitly distinct categories in Hecate's API surface.

### 6. Roadmap alignment (no new feature IDs)

This ADR lands as implementation discipline over existing items, not new catalog entries:

- **Phase A (P3)**: dogfood — Ops Center consumes public APIs only; internal evaluators all register via EvaluatorBase; models all via ModelPluginBase/configuration. Event-plane schema is semver'd from 1.3.19 day one.
- **Phase B (P4)**: isolation inversion (isolated-by-default, in-process as exception) + WASM (6.41) promotion + panel/node-pack plugin types + installation trust marks (recorded as 5.5 enhancement note).
- **Phase C (P5)**: market (12.0) + scanning/signing (5.13) + pin-by-hash (5.9 hardening already recorded).

## Consequences

**Positive**
- Multi-tenant safety is structural: ecosystem code can never reach tenant data by construction (tier ladder), closing the ClawHub-class risk before a marketplace exists.
- The event log becomes a genuine platform bus; observability/evaluation/marketplace all consume one versioned contract — cheaper than API-polling plugin architectures.
- "Batteries included, but replaceable" preserves the enterprise out-of-box story while every layer stays contestable — the strategic position between Omnigent (too abstract) and Dify (no engine).
- Roughly half the ladder already exists (T1 sandbox pool, T2 MCP, T4 skill loader, T3 WASM scheduled); the true net-new work is T0 discipline and install-time trust marks.

**Negative / costs**
- Contract maintenance tax: each plane is a public semver'd surface; the event schema becomes an external commitment the day 1.3.19 ships.
- Dogfood discipline slows first-party feature work slightly (building defaults against plugin contracts).
- Some legitimate partner needs (per-token custom processors from third parties) are pushed to fork-extensions until a future ADR revision — deliberate: that cost is visible, and revising the ADR later with real demand is cheaper than eroding the isolation default now.

**Deferred decisions (explicit)**
- Signed-partner T0 exception: **closed as "no exceptions"**; may only be reopened by a future ADR revision with real demand and a review-SLA design. Signature proves identity, not behavior (SolarWinds/ClawHavoc precedent).
- Third-party studio support: possible-but-unsupported; escalation to a supported plane requires customer evidence.
