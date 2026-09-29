# Positioning & Competitive Landscape

This document explains **where Hecate sits in the agent platform landscape**, what differentiates it from neighboring tools, and when to choose it (or not). It is meant for engineering evaluators, enterprise architects, and contributors who need to explain Hecate's strategic position.

Hecate is currently in **alpha**. The positioning is provisional — it will sharpen as the project matures toward 1.0.

The strategic target is being revised toward an **enterprise Agent governance and integration platform** whose execution components can also be consumed by products without deploying the management plane. This target describes the direction under review; it does not mean independent Runtime distribution or production governance profiles are already shipped. The [platform evolution plan](../refactor/enterprise-agent-platform-evolution-plan.md) tracks that work and its acceptance gates. This page's detailed feature and competitor claims remain subject to a step2 evidence and catalogue review.

---

## 30-second summary

> Hecate's target is an **open-source enterprise Agent governance and integration platform**: organizations can register and govern Agents built with different runtimes, models, and languages, while products can consume a separately deployable execution component when they need one. The current implementation is Python-based and includes a Pregel Runtime, APIs, MCP/A2A integrations, and enterprise capabilities at varying maturity. Hecate remains in alpha; independent Runtime distribution, cross-runtime governance, and production support profiles require the implementation and evidence in the evolution plan. Python, Pregel, and any one provider are reference choices, not requirements imposed on every connected Agent.

---

## Delivery boundaries and deployment modes

Hecate separates what it must guarantee (governance semantics, trust boundaries, enforcement points, verifiable evidence) from what it ships as replaceable implementations. Three delivery boundaries follow; none of them requires a separate repository or an immediate microservice split, and no single algorithm, database, or vendor SDK may become a mandatory dependency across all boundaries.

| Delivery boundary | Minimum responsibility and callers | Optional parts and exclusions |
|---|---|---|
| Neutral contracts and clients | Publish schemas, request/event/receipt samples, and versioning rules per capability; usable by business apps, the management platform, and per-language adapters | SDKs are convenience wrappers — they never pull in full Hecate, the ORM, the Pregel DSL, or vendor-private state |
| Standalone execution component | Built-in Runtime plus an execution host that loads definitions, assembles selected adapters, verifies caller identity and permissions, manages execution state and local evidence, and serves a business-app entry point | Model implementations, Memory/RAG, sandbox, persistence, and security services are configurable; the standalone host needs neither Studio, an org directory, nor the management platform to start |
| Enterprise management platform | Registration/deployment, centralized admission, release, teams, human intervention, evaluation management, and evidence queries | Manages built-in and external runtimes; customers never need the management platform just to use the reference execution component |

Three deployment modes are accepted separately — passing one never counts as passing another:

| Deployment mode | Must deploy | Source of definitions, authorization, and state | First delivery gate |
|---|---|---|---|
| Standalone | Execution host, selected model/business adapters, profile-required storage | Locally pinned artifacts; the business app's verified identity and local policy; the host owns local tasks and execution facts | Read-only technical preview on the evolution plan's standalone path; scoped production only after its reliability/governance slice passes |
| Managed | The same execution host or a qualified external backend, plus a reachable (or policy-disconnected) control plane | Control plane publishes desired configuration and authorization; the executor keeps actual bindings, execution facts, and command receipts | After the managed connect/disconnect/reconnect slice passes its gates |
| Full private platform | Management platform, execution components, and chosen infrastructure inside the customer environment | The local enterprise domain owns centralized governance; the executor still owns execution facts | Per the full-platform conformance profile |

**Consumer contract for business-App standalone delivery** (example scenario: a structured inventory-type business app). The business API keeps final authority over inventory reads/writes and the business state machine; the standalone host never depends on the example business's data model, and Hecate implements no inventory, pricing, or customer management. Delivery gates are tiered: a read-only technical preview (no production writes), scoped standalone production (local identity/approval, durable tasks, evidence retention), and managed production (control-plane enrollment with bounded authorization leases). The first standalone service entry point is HTTP/JSON; in-process Python embedding is certified separately against its own lifecycle and isolation constraints; no up-front promise is made about SDKs in other languages.

**Hosted Agent services are a first-class execution backend.** Vendor-hosted Agent services (which hold the reasoning loop and session) register on two axes — harness/session owner and sandbox/file-and-command owner — plus the tool-and-data enforcement point, instead of a single hosted/self-hosted tag. Self-hosting Hecate does not make every bound backend satisfy private-deployment or local data-residency requirements; vendor conditions (residency, retention, deletion, revocation effectiveness) are admission inputs verified per service version at binding time.

---

## The agent platform landscape

Agent products span hosted platforms, self-hosted systems, execution frameworks, automation tools, and coding agents. These categories overlap; this diagram is orientation, not a complete taxonomy or a claim that Hecate uniquely combines them.

```
                         ┌──────────────────────────────────────────────────────────────┐
                         │                    Visual-first SaaS                        │
                         │              Dify · Salesforce Agentforce                  │
                         │              AWS Bedrock AgentCore · 智果                 │
                         │              IBM watsonx Orchestrate · Google              │
                         │              Gemini Enterprise · Palantir AIP            │
                         │    "Build agents by clicking · pay per usage"            │
                         └──────────────────────────────────────────────────────────────┘
                                              ▲
                                              │ Hecate competes with both
                                              ▼
  ┌──────────────────────────────┐    ┌───────────────────────────────────────┐
  │     Code-first frameworks     │    │       Workflow automation              │
  │  LangGraph · CrewAI · AutoGen │    │  n8n · Apache Airflow · Temporal     │
  │  "Build agents in code, you  │    │  "Orchestrate anything, agents are    │
  │   bring the runtime"          │    │   a feature"                          │
  └──────────────────────────────┘    └───────────────────────────────────────┘
                          ▲                              ▲
                          │ Hecate absorbs good ideas from both
                          ▼                              ▼
         ┌────────────────────────────────────────────────────────────┐
         │              Coding assistants / AI IDEs                     │
         │  Claude Code · Codex · Hermes Agent · Meituan CatPaw      │
         │  "Agent that codes for you in your terminal / IDE"         │
         └────────────────────────────────────────────────────────────┘
```

Hecate is **not** a coding assistant — it is a platform for building production agents. Coding assistants use Hecate's API surface (OpenAI-compatible) to talk to the LLM, but they are a different product category.

---

## Comparison matrix

| Dimension | **Hecate** | Dify | LangGraph | CrewAI | Salesforce Agentforce | AWS Bedrock AgentCore | n8n |
|---|---|---|---|---|---|---|---|
| **Deployment** | Self-hosted OSS (MIT) | Cloud + self-host | OSS library | Cloud + enterprise | Cloud SaaS | Cloud (AWS) | Cloud + self-host (Fair-code) |
| **Primary UX** | Code (Python) + Visual | Visual-first | Code (Python) | Code + Visual | Visual + code | Code (any framework) | Visual + code |
| **Engine** | Built-in Pregel/BSP Runtime (the reference implementation) + event-sourced execution state (Log-as-Truth); external execution backends plug in through versioned contracts | DAG-based | Pregel (Google) inspired | Custom | Atlas Reasoning Engine | Wraps frameworks | DAG-based |
| **MCP server + client** | ✅ Bidirectional (latest spec) | ✅ Client only | Partial | ✅ | ✅ | ✅ | ✅ |
| **A2A protocol** | ✅ (server + client) | ❌ | ❌ | Partial | ✅ | ✅ | ❌ |
| **OpenAI-compatible API** | ✅ Wire-compatible | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |
| **Multi-tenancy native** | ✅ Org → Workspace → RBAC | ✅ | ❌ Add-on | ✅ | ✅ | ✅ (AWS accounts) | ✅ |
| **Visual canvas** | ✅ (`web/`) | ✅ | ❌ | ✅ | ✅ | ❌ | ✅ (primary UX) |
| **Execution extensibility** | Built-in Pregel Runtime today; target accepts external backends through versioned contracts | Plugins | Library APIs | Framework APIs | Product-specific | Bring your framework | Nodes |
| **Self-evolution loop** | ✅ skill-package loop: eval-gate + human review before any learned skill persists (1.3.6f) | ❌ | ❌ | ❌ | Partial (frozen-weight whitepaper + observability) | ✅ prompt-optimization API (A/B + promote) | ❌ |
| **Runtime hallucination mitigation** | ✅ citation provenance layer (tool-result chunk markers + session-resolvable registry + uncited-claim risk signal) + grounding scoring layer (dual-backend entailment seam, fallback retrieval, three-way verdicts, shadow disposition) (1.3.5e Stage 1-2); disposition actions staged next | ❌ | ❌ | ❌ | Partial (Einstein Trust Layer grounding, no public scoring API) | ✅ Guardrails contextual grounding check (caller supplies the grounding source) | ❌ |
| **Target user** | Engineers building internal agent platforms | Business / non-developers | Engineers prototyping | Mixed business + engineers | Enterprise admins | AWS-native engineers | Ops + IT |
| **Pricing** | Free (self-host) | Free tier + cloud | Free (OSS) + paid platform | Enterprise | Per-conversation | Pay-per-use AWS | Free self-host + cloud |
| **License** | MIT | Apache-2.0 + cloud | MIT (LangGraph) + proprietary (LangSmith) | Proprietary | Proprietary | Proprietary | Sustainable Use License |
| **GitHub stars** (Aug 2026) | small (alpha) | ~110k | ~18k (langgraph) | ~30k | n/a (closed) | n/a (closed) | ~200k |

> Stars shown for context only — Hecate is alpha and doesn't compete on popularity.

---

## Hecate vs each major alternative

### Hecate vs Dify

| | Dify | Hecate |
|---|---|---|
| **Mental model** | Visual canvas, drag nodes | Python API + optional visual canvas |
| **Who builds** | Business analysts, PMs | Engineers |
| **When to choose Dify** | You want a team of non-developers shipping chatbots in a week | |
| **When to choose Hecate** | | You need engine-level control (custom scheduler, custom guardrail hooks, custom checkpoint store) — Dify's DAG abstraction hides too much |

**Hecate's advantage**: Full source code, **many engine extension interfaces (multiple Core + multiple SPI)** per [ADR-016](adr/016-platform-spi-architecture.md) at the engine layer. Dify's extension model is via Marketplace plugins, not engine-level SPI.

**Dify's advantage**: Faster time-to-first-chatbot for non-developers. Larger community (~110k stars vs Hecate's alpha-stage visibility).

---

### Hecate vs LangGraph

| | LangGraph | Hecate |
|---|---|---|
| **Mental model** | Python library — you bring the runtime | Platform — runtime included |
| **Production deployment** | Pair with LangSmith Deployment (paid) | Self-hosted; production support is granted per deployment profile as evidence lands (alpha today) |
| **MCP** | Partial (client) | Bidirectional (server + client, latest spec) |
| **A2A** | ❌ | ✅ (server + client) |
| **Multi-tenancy** | Add-on (you build it) | Native (Org → Workspace → RBAC) |
| **When to choose LangGraph** | You only need a Python library and want to deploy on LangChain's infrastructure | |
| **When to choose Hecate** | | You need a self-hosted platform with MCP/A2A/multi-tenancy/OpenAI-API already wired |

**Hecate's advantage**: It's a **platform**, not a library. Out of the box: 100+ LLM providers via LiteLLM, multi-tenancy, MCP server + client, A2A server + client, OpenAI-compatible API, visual canvas. With LangGraph, you build all of this yourself.

**LangGraph's advantage**: Ecosystem — LangSmith for observability, LangChain for integrations, LangGraph Studio for visual debugging. Production deploy path is well-trodden (Klarna, Uber, J.P. Morgan).

**Interesting trivia**: LangGraph's docs explicitly say it's "inspired by [Pregel](https://research.google/pubs/pub37252/) and [Apache Beam](https://beam.apache.org/)". Hecate's runtime is also Pregel-inspired (and named for it) — but as a self-developed runtime, not a library.

---

### Hecate vs CrewAI

| | CrewAI | Hecate |
|---|---|---|
| **Mental model** | "Role-playing crews" — declarative agent + task | Multi-agent orchestration via graph DSL or code |
| **Status** | Commercial product with enterprise tier | OSS in alpha |
| **Customers (claimed)** | DocuSign, Experian, Pepsico, IBM, ABInBev (65% Fortune 500) | None yet (alpha) |
| **When to choose CrewAI** | You want a managed enterprise platform with sales motion | |
| **When to choose Hecate** | | You want OSS, self-hosted, no per-seat fees, code-first |

**Hecate's advantage**: Truly OSS (MIT) and self-hosted. CrewAI is moving toward commercial / closed-source.

**CrewAI's advantage**: Mature enterprise product, 65% Fortune 500 claim (verify directly), managed runtime.

---

### Hecate vs Salesforce Agentforce

| | Agentforce | Hecate |
|---|---|---|
| **Deployment** | Salesforce-managed cloud | Self-hosted OSS |
| **Target customer** | Enterprises already on Salesforce CRM | Engineering teams building their own platform |
| **Pre-built agents** | Service Agent, SDR, Sales Coach, Buyer Agent, etc. | None — you build |
| **Data integration** | Native Salesforce Data Cloud | Bring your own (Postgres, Qdrant, MinIO) |
| **Compliance** | SOC 2, HIPAA, FedRAMP | Self-managed (you do the audit) |
| **When to choose Agentforce** | You're a Salesforce shop; "low-code builder" matches your team | |
| **When to choose Hecate** | | Data residency / on-prem is mandatory; or you don't want Salesforce lock-in |

**Hecate's advantage**: Compliance posture is yours to define — useful for industries (defense, healthcare, finance) where Salesforce cannot host the data.

**Agentforce's advantage**: Named agents for common roles (Service Agent, Sales Coach, etc.) give you a head start. Heavy investment in compliance certifications.

---

### Hecate vs AWS Bedrock AgentCore

| | Bedrock AgentCore | Hecate |
|---|---|---|
| **Deployment** | AWS-managed | Self-hosted OSS |
| **Scope** | Production runtime for any agent framework (LangChain, Strands, Claude Agent SDK, OpenAI Agents SDK) | Engine + platform in one |
| **Lock-in** | AWS account, VPC, IAM | None |
| **When to choose AgentCore** | You're already on AWS and want managed runtime for any framework | |
| **When to choose Hecate** | | You're not on AWS, or you want engine-level extensibility (custom scheduler, custom guardrail hooks) — AgentCore abstracts these away |

**Hecate's advantage**: Engine-level SPI vs framework-level runtime. Custom Scheduler, custom RetryStrategy, custom ConflictResolver — AgentCore doesn't expose these (they're abstracted).

**AgentCore's advantage**: Framework-agnostic — bring LangChain, Strands, or anything else. AWS-native IAM/SOC2/HIPAA/FedRAMP.

---

### Hecate vs n8n

| | n8n | Hecate |
|---|---|---|
| **Mental model** | General-purpose workflow automation with an AI feature | Agent platform |
| **Primary use** | "Move data between SaaS apps" + now "build AI agents" | "Build agents that interact with LLM tools and data" |
| **Visual model** | Workflow nodes (each does one thing) | Agent graph DSL (nodes are reasoning steps, not data transforms) |
| **License** | Sustainable Use License (not OSI-approved) | MIT |
| **GitHub stars** | ~200k | small (alpha) |
| **When to choose n8n** | You need general workflow automation and AI agents are a feature among many | |
| **When to choose Hecate** | | You are building an **agent-first** product where workflows serve the agent (not the other way around) |

**Hecate's advantage**: Agent-first design — graph DSL is centered on reasoning, not data transforms. MIT license.

**n8n's advantage**: 200k stars, 500+ integrations, mature community. AI agents are a recent addition to a workflow tool.

---

### Hecate vs n8n's positioning line

n8n's own marketing says: *"Tools like ChatGPT and Claude are great, but n8n is the thing that allows you to integrate AI into your work and your processes in a safe and controlled way."*

Hecate's positioning line:

> *"Tools like LangGraph and Dify are great at prototyping agents, but Hecate is the platform that lets you **ship** them — self-hosted, multi-tenant, OpenAI-compatible, with the protocols (MCP, A2A) wired in and the engine (**many interfaces: multiple Core + multiple SPI**) open for extension."*

---

### Hecate vs Chinese platforms (openjiuwen, 智果AgentArts, AgentScope)

| | openjiuwen | 智果 AgentArts | AgentScope | Hecate |
|---|---|---|---|---|
| **Origin** | Huawei (open-sourced) | Huawei Cloud (commercial) | Alibaba (DAMO) | Independent |
| **Deployment** | OSS + cloud | Cloud only | OSS library | OSS |
| **Visual canvas** | ✅ | ✅ | ❌ | ✅ |
| **Engine-level SPI** | Limited | Limited | Limited | ✅ many (multiple Core + SPI) |
| **Multi-tenancy** | Single-tenant | Multi-tenant | Single-tenant | ✅ Native |
| **When to choose** | You want Huawei ecosystem + Chinese community | You want cloud-managed + Chinese compliance | You want Alibaba-aligned research | You want full control + open protocol surface |

**Hecate's positioning in China**: Independent (no cloud vendor lock-in), MIT-licensed, full protocol surface (MCP + A2A + OpenAI API), engine-level SPI — positioned as the "**Linux of agent platforms**" for teams that don't want Huawei/Alibaba/Bytedance vendor alignment.

---

## When to choose Hecate

Consider Hecate when these needs matter:

1. You need an enterprise layer for Agent identity, access, approval, audit, evaluation, and lifecycle across different execution technologies.
2. You need to self-host the management plane or consume an execution component from a product you operate.
3. You want to choose Runtime, Memory/Knowledge, evaluation, observability, sandbox, and gateway implementations independently, subject to tested compatibility.
4. Your Agents use protocols such as MCP or A2A, and you need enterprise identity and action controls around those connections.
5. You want an open-source reference implementation and can assess its current alpha maturity against your deployment requirements.

## When NOT to choose Hecate

Pick something else when:

| If you want... | Choose | Why |
|---|---|---|
| Non-developers building chatbots in days | **Dify** | Visual-first, faster time-to-first-chatbot |
| A library, not a platform | **LangGraph** | Lighter weight; you bring the runtime |
| Managed cloud with sales motion | **CrewAI** or **Salesforce Agentforce** | Production-ready managed product |
| AWS-native runtime for any framework | **Bedrock AgentCore** | AWS-native IAM, framework-agnostic |
| General workflow automation (AI is a feature) | **n8n** | 500+ integrations, broader scope |
| Coding assistant in terminal/IDE | **Claude Code / Codex / Hermes** | Different product category entirely |
| Chinese cloud SaaS with templates | **智果 AgentArts** | Chinese compliance + visual templates |

---

## Strategic positioning summary

Hecate's target position is the **enterprise integration and governance layer around Agents**, with an optional, independently consumable reference execution component. It should let organizations use different frameworks, hosted Agent services, models, and languages while applying consistent identity, policy, approval, evidence, evaluation, and lifecycle rules where the connected backend permits those controls.

The built-in Pregel Runtime remains a product capability and a reference implementation. Its event-sourced execution model, context processing, workflow tooling, and other engine features can differentiate Hecate's own Runtime; external Agents do not need to adopt its graph DSL or internal state model. The Runtime's current import boundaries do not by themselves prove it can be installed and run independently. The [evolution plan](../refactor/enterprise-agent-platform-evolution-plan.md) defines that work and separates a standalone execution profile from the management platform.

Hecate's governing promise must follow evidence: registration is not production approval, a provider's declared feature is not enforcement, and a trace is not proof that an action was authorized. Each backend and deployment combination earns only the control and support level demonstrated by conformance tests. The platform may report limited observability or control for hosted systems rather than infer hidden provider behavior.

### Reading feature priorities and delivery status

The **P1→P5 catalogue** remains in [feature-catalog.md](../features/feature-catalog.md), with sequencing in [roadmap.md](../features/roadmap.md). Those files contain historical implementation priorities and need the step2 reclassification defined in the evolution plan; their existing priority or ✅ markers do not certify a production capability, an independent package, or acceptance of the new target architecture. The current plan's first independent-consumer path is baseline → contracts → shared Runtime assembly → standalone preview → local reliability/governance → scoped production conformance. Team federation, public ecosystem distribution, and bounded self-governance are conditional follow-on work, not prerequisites for that first path.

**Observability differentiator (8.20 Execution Replay)**: On top of the event-sourced substrate, Hecate ships a built-in **execution replay dashboard** — trace-partitioned timeline (vocabulary: `session → trace → event`, aligning with LangFuse/LangSmith/IBM rather than the unanchored "runId" that competitors often leave ambiguous), DAG step-through, and fold-to-version time-travel ("show me what the model saw at step N"). Pure read-side consumer of the enriched log: zero schema change, tenant-scoped, no extra runtime hook. Guardrail blocks are derived from synthetic tool-error messages (Phase 1) and upgrade cleanly to the planned waterfall middleware stage events (1.3.5i E3) when shipped. Empty-log sessions (path A/C calls) hide the tab rather than render an empty view; UI labels coverage boundaries so users aren't misled about replay semantics. Time-travel reuses the same `fold_session` path that live mutation uses, eliminating projection drift between replay and execution.

**Sandboxed browser automation (6.27)**: Agents drive a real headless Chromium (`browser_navigate/click/type/extract/screenshot/fill_form`) inside a dedicated Docker sandbox with per-environment domain allow-lists enforced fail-closed — navigation outside `allowedDomains` is refused before any network request leaves the container and upgrades the tool call to HIGH risk for approval. Framework-only competitors (LangGraph, CrewAI) leave browser tooling entirely to userland; SaaS competitors either omit it or run it outside the customer's trust boundary. Hecate's version inherits the platform's guardrail hooks, DLP scanning (text + screenshots), audit pipeline, and risk gating for free because it is a builtin tool on the standard `ToolRegistry` path.

**Context engineering as pluggable policy (4.13)**: Hecate projects conversation history through an ordered, budget-satisfied processor chain before every LLM call — atomic tool-call grouping, provider-usage-anchored token estimation, tool-result truncation, KV-cache-aware prefix protection with cache-breakpoint hints rendered per provider, offload with an explicit recall tool, compression, and budget warn hints the model can act on. Policy resolves per node (node > agent > model-capability > platform defaults) with load-time fail-fast validation and a canonical policy hash bound to agent versions; degradation ends in controlled termination (``token_capped``) instead of silent truncation, and every level lands in the queryable budget snapshot. Enterprise platforms keep context management as a black box (Bedrock AgentCore, Agentforce, watsonx Orchestrate) and frameworks leave it to userland middleware (LangGraph, CrewAI); Hecate makes context policy a governed, versioned, per-model configuration asset. Durable, log-level compaction via surface replacement — bracket events, a shadowing ledger with anchor fail-open and rolling re-compaction, a capacity-axis trigger, and a bracket lock — is implemented per [ADR-033](adr/033-context-compaction-surface-replacement.md) on the same event-sourced substrate: the channel and the fold stay untouched, the working surface is a per-invocation derived view, and every compaction is auditable in the log. No surveyed peer combines durable compaction with an audit-preserving log lock.

**Governed intent routing (6.23 ⊕ 1.3.10, 6.49)**: Hecate's engine classifies intent in layers — per-turn atomic classification through an ordered fast path (decision cache → package patterns → few-shot evidence → LLM fallback), multi-turn workflow-intent accumulation, and a durable session goal — and routes through a central controller node with an explicit default-workflow fallback. The distinguishing piece is the **intent package asset layer**: categories plus sample utterances as governed workspace assets with named immutable versions, a deterministic publish gate (deterministic accuracy/coverage signals only), and correction backflow with provenance riding the platform's evaluation machinery. Competitors ship pieces of this — Dify/Coze expose intent-classification nodes whose configuration is private to each node, Salesforce Agentforce makes Topics first-class but without sample-utterance governance, Bedrock AgentCore names a Routing Classifier pattern — but none ship versioned, gate-governed intent sample assets that the recognition engine consumes read-only at runtime.

When Hecate wins an evaluation, it is almost always because of one of these triggers:

1. "We can't send our data to a SaaS" → **self-hosted**
2. "We need custom guardrails / scheduler / checkpoint store" → **engine SPI**
3. "We're already using MCP and A2A internally, give us a platform that speaks them" → **protocol surface**
4. "We have 50 engineers; we need RBAC and audit trails" → **multi-tenancy**

When Hecate loses an evaluation, it is almost always because of:

1. "We need to ship a chatbot in two weeks, not invest in a platform" → **Dify**
2. "We want a managed service with a sales contact" → **Agentforce / CrewAI**
3. "Our team is 3 engineers; we don't need a platform" → **LangGraph**
4. "We're already on AWS / Salesforce / Huawei Cloud" → **respective native platform**

---

## What this document is NOT

This document deliberately avoids:

- **Pricing comparisons** — Hecate is free; commercial platforms have complex per-seat/per-token pricing that changes. Get a quote from each vendor for your workload.
- **Performance benchmarks** — LLM throughput, token latency, etc. depend on hardware, model choice, and workload. Run your own benchmarks.
- **"X is better than Y" claims** — Each platform has real strengths; the choice is about fit. The "When NOT to choose Hecate" table is the honest version of this.

If a fact in this document is wrong or out of date, please open an issue or PR — the landscape changes fast and we want this to be accurate.

---

## References

Sources for the claims in this document :

- Dify: [dify.ai](https://dify.ai/), GitHub README, Apache-2.0 LICENSE
- LangGraph: [docs.langchain.com/oss/python/langgraph](https://docs.langchain.com/oss/python/langgraph/overview), Klarna/Uber/J.P. Morgan customer references
- CrewAI: [crewai.com](https://www.crewai.com/), 65% Fortune 500 claim, Fortune 500 customer logos
- Salesforce Agentforce: [salesforce.com/agentforce](https://www.salesforce.com/agentforce/), Gartner MQ 2026 mention
- AWS Bedrock AgentCore: [aws.amazon.com/bedrock/agentcore](https://aws.amazon.com/bedrock/agentcore/), Cox Automotive / Druva / Thomson Reuters case studies
- IBM watsonx: [ibm.com/watsonx](https://www.ibm.com/watsonx), Gartner MQ 2026 AI Governance mention, US Open/Vodafone/D&B case studies
- n8n: [n8n.io](https://n8n.io/), 200k GitHub stars, Sustainable Use License
- 智果 AgentArts: [huaweicloud.com/product/agentarts](https://www.huaweicloud.com/product/agentarts.html), case studies (温氏食品/青岛港/万华化学/太平洋保险/晋云煤矿)
- openjiuwen: [openjiuwen.com](https://openjiuwen.com/), JiuwenSwarm / Coordination Engineering concepts
- AgentScope: [github.com/agentscope-ai/agentscope](https://github.com/agentscope-ai/agentscope), arxiv papers
