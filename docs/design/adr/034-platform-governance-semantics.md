# ADR-034: Platform Governance Semantics and Execution Backend Standing

## Status

Accepted (2026-09-29; consolidates the governance decisions of the enterprise agent platform evolution plan into a single record; complements ADR-029's kernel/plugin isolation tiers with the cross-backend governance contract)

## Context

Hecate's strategic target is an enterprise Agent governance and collaboration platform whose execution components can also be consumed without deploying the management plane (see [positioning](../positioning.md) and the evolution plan). This raises six architectural questions that earlier ADRs answered only implicitly or not at all:

1. Which results must Hecate guarantee versus which implementations may be replaced — and can "replaceable" erode governance?
2. What is the standing of the built-in Pregel runtime once external execution backends exist?
3. How are vendor-hosted Agent services (which hold the reasoning loop and session) registered without pretending the platform controls what it cannot?
4. Who owns which pieces of task, run, and session state between the control plane, executors, and vendor sessions?
5. How do heterogeneous backends expose uneven capabilities without a lowest-common-denominator contract?
6. Can running state be migrated losslessly when a backend changes?

Answering these per-feature or per-PR has produced drift; step3's execution-backend contract and step5's standalone distribution need them fixed as prior decisions.

## Decision

### 1. Hecate guarantees governance results; implementations are replaceable

Hecate's core deliverable is the set of cross-implementation governance semantics — identity and delegation context, agent registration and deployment governance, task/run control, team delegation, action policy and approval, release and quality gates, governance evidence, ecosystem admission — each with a named enforcement point, a single authoritative state writer, and verifiable evidence. Default implementations, enterprise-existing systems, and third-party services all participate through contracts. "Replaceable" never means the guarantees may be skipped: a backend that cannot prove identity, protected action paths, or required evidence is limited to isolated trials, observational registration, or rejection for the corresponding use. Registration is not production approval; a provider's declared feature is not enforcement; a trace is not proof an action was authorized.

### 2. The built-in Pregel Runtime is the reference implementation

The built-in Runtime remains a product capability and the reference implementation. Its engine features (event-sourced execution state per ADR-030, context processing, workflow tooling, replay) differentiate Hecate's own runtime and are exposed through capability declarations; external Agents and backends never need to adopt its Graph DSL, channels, or internal state model, and no contract may require Pregel internals. The platform-to-runtime direction uses execution-backend contracts; the existing engine-to-services `RuntimePort` keeps its current direction.

### 3. Hosted harnesses and environments register on independent axes

Vendor-hosted Agent services are a first-class execution backend. Each deployment records the **harness/session owner** and the **sandbox/file-and-command owner** as separate axes, plus the **tool-and-data enforcement point** — never a single `hosted`/`self-hosted` tag. Vendor conditions (data residency, retention/deletion, network egress, revocation effectiveness, internal tooling) are admission inputs verified against the specific service version at binding time; self-hosting one axis does not privatize the other. The platform claims only control it actually exercises and records vendor-only controls as observed or uncontrolled.

### 4. State and session ownership is single-writer

The control plane owns platform task responsibility/acceptance, deployment desired configuration, and governance records; executors own actual run lifecycle, checkpoints, internal loops, and action results; the platform stores executor state only as projections carrying source and sequence. A vendor session/turn is not a platform Task/Run — the mapping is stored, not merged, and vendor-internal subagents are execution details, not enterprise team members. In standalone mode the execution host is the authoritative owner of local task/run/action records. Every field has exactly one authoritative writer; dual-writer setups are prohibited regardless of deployment shape.

### 5. Capabilities are negotiated per item, not flattened

The minimum cross-backend contract stays small and stable; advanced abilities (pause, resume, tool interception, sandbox, stage diagnostics, …) are exposed through per-item capability declarations with verifiable control semantics — `unsupported` / `cooperative` / `enforced` — certified per capability, per deployment, per version. A "supports governance" boolean is prohibited; API acceptance of a request is not proof of enforcement. Vendor-specific abilities live in named extension namespaces and never leak into the minimum contract.

### 6. No lossless running-state migration is promised

Backend or version changes route **new tasks**; running tasks stay pinned to their original binding and version. Context export followed by re-execution is recorded as a new run, not a continuation. Emergency revocation and hard denials known to the enforcement point override pinned authorization snapshots; version pinning must never block revocation. A disconnected managed host cannot promise instant delivery of central revocations: scoped leases, expiry, trustworthy time and a declared maximum stale window bound this limitation; actions needing instant revocation require online decisions. Reconnection never grants standalone self-authorization or migrates active runs. Capability or topology changes require re-certification of the affected deployment combinations.

## Consequences

- step3's `AgentExecutionBackend` contract, capability model, and event envelope implement decisions 2, 5, and the platform-side of 4; step4's deployment/task/run model implements 3 and 4; step7 enforces 1 across entries; step8 certification gates apply 3, 5, and 6 to real backends.
- Features that cannot meet a guarantee are scoped, degraded with explicit labels, or rejected for the affected use — not silently accepted.
- ADR-029's trust-tiered kernel/plugin tiers continue to govern in-process extension; this ADR governs the cross-backend seams above them.
