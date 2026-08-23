# Positioning & Competitive Landscape

This document explains **where Hecate sits in the agent platform landscape**, what differentiates it from neighboring tools, and when to choose it (or not). It is meant for engineering evaluators, enterprise architects, and contributors who need to explain Hecate's strategic position.

Hecate is currently in **alpha**. The positioning is provisional — it will sharpen as the project matures toward 1.0.

---

## 30-second summary

> Hecate is an **open-source, self-hosted, Python-first agent platform** for engineering teams building **production agents** that need to live inside an organization's own infrastructure. It is **not** a no-code SaaS (use Dify for that) and **not** a thin framework (use LangGraph if you only need an orchestration library). It is the **engine-level platform layer in between**: a self-developed Pregel runtime, multi-tenant by default, OpenAI-compatible on the API surface, MCP + A2A on the protocol surface, and 26 engine extension interfaces + 8 plugin SPI types when you need to go deep.

---

## The agent platform landscape

There are roughly **four categories** of tools in this space. Hecate is the only one that combines **all four** in a single project:

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
| **Engine** | Self-developed Pregel/BSP + event-sourced execution state (Log-as-Truth, 1.3.19) | DAG-based | Pregel (Google) inspired | Custom | Atlas Reasoning Engine | Wraps frameworks | DAG-based |
| **MCP server + client** | ✅ Bidirectional (2026-07-28 spec) | ✅ Client only | Partial | ✅ | ✅ | ✅ | ✅ |
| **A2A protocol** | ✅ (server + client) | ❌ | ❌ | Partial | ✅ | ✅ | ❌ |
| **OpenAI-compatible API** | ✅ Wire-compatible | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |
| **Multi-tenancy native** | ✅ Org → Workspace → RBAC | ✅ | ❌ Add-on | ✅ | ✅ | ✅ (AWS accounts) | ✅ |
| **Visual canvas** | ✅ (`web/`) | ✅ | ❌ | ✅ | ✅ | ❌ | ✅ (primary UX) |
| **Engine-level extensibility** | ✅ 26 interfaces + 8 SPI | Plugins | Decorators | Limited | Limited | Bring your framework | Nodes |
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

**Hecate's advantage**: Full source code, 26 engine extension interfaces + 8 plugin SPI types at the engine layer. Dify's extension model is via Marketplace plugins, not engine-level SPI.

**Dify's advantage**: Faster time-to-first-chatbot for non-developers. Larger community (~110k stars vs Hecate's alpha-stage visibility).

---

### Hecate vs LangGraph

| | LangGraph | Hecate |
|---|---|---|
| **Mental model** | Python library — you bring the runtime | Platform — runtime included |
| **Production deployment** | Pair with LangSmith Deployment (paid) | Self-hosted, production-ready out of the box |
| **MCP** | Partial (client) | Bidirectional (server + client, 2026-07-28 spec) |
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

> *"Tools like LangGraph and Dify are great at prototyping agents, but Hecate is the platform that lets you **ship** them — self-hosted, multi-tenant, OpenAI-compatible, with the protocols (MCP, A2A) wired in and the engine (26 interfaces + 8 SPI) open for extension."*

---

### Hecate vs Chinese platforms (openjiuwen, 智果AgentArts, AgentScope)

| | openjiuwen | 智果 AgentArts | AgentScope | Hecate |
|---|---|---|---|---|
| **Origin** | Huawei (open-sourced) | Huawei Cloud (commercial) | Alibaba (DAMO) | Independent |
| **Deployment** | OSS + cloud | Cloud only | OSS library | OSS |
| **Visual canvas** | ✅ | ✅ | ❌ | ✅ |
| **Engine-level SPI** | Limited | Limited | Limited | ✅ 26 interfaces + 8 SPI |
| **Multi-tenancy** | Single-tenant | Multi-tenant | Single-tenant | ✅ Native |
| **When to choose** | You want Huawei ecosystem + Chinese community | You want cloud-managed + Chinese compliance | You want Alibaba-aligned research | You want full control + open protocol surface |

**Hecate's positioning in China**: Independent (no cloud vendor lock-in), MIT-licensed, full protocol surface (MCP + A2A + OpenAI API), engine-level SPI — positioned as the "**Linux of agent platforms**" for teams that don't want Huawei/Alibaba/Bytedance vendor alignment.

---

## When to choose Hecate

Pick Hecate when **all** of these are true:

1. **Self-hosted is required** (data residency, compliance, or cost reasons)
2. **Multi-tenancy is required** (you're building a platform for many teams / customers)
3. **Protocol surface matters** (you need MCP, A2A, OpenAI-compatible — not just one)
4. **Engine-level extensibility is required** (you'll write custom scheduler, guardrail hooks, or checkpoint store)
5. **MIT-licensed OSS is required** (no per-seat fees, no telemetry)

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

Hecate occupies **the engineering platform niche** between:

- **Frameworks** (LangGraph, CrewAI, AutoGen) — too low-level; you bring the runtime
- **SaaS platforms** (Dify Cloud, Agentforce, Bedrock) — too high-level; no source code

It is the **Linux of agent platforms**: self-hosted, MIT-licensed, engine-level control, multi-protocol, multi-tenant. The audience is engineering teams building internal agent platforms or shipping agents as a product.

**Engine differentiator**: Hecate's runtime is **event-sourced (Log-as-Truth, [ADR-030](adr/030-event-sourced-execution-state.md))** — the event log, not per-step snapshots, is the source of execution state. Execution state is replayable (WAL ordering, `STEP_END` commit points), checkpoints are materialized caches, and interrupt/resume is log-derived. This is the substrate for execution replay (8.20), durable HITL audit pairs (1.3.4), and middleware waterfall events (1.3.5i E3) that no competitor's engine ships today.

**Observability differentiator (8.20 Execution Replay)**: On top of the event-sourced substrate, Hecate ships a built-in **execution replay dashboard** — trace-partitioned timeline (vocabulary: `session → trace → event`, aligning with LangFuse/LangSmith/IBM rather than the unanchored "runId" that competitors often leave ambiguous), DAG step-through, and fold-to-version time-travel ("show me what the model saw at step N"). Pure read-side consumer of the enriched log: zero schema change, tenant-scoped, no extra runtime hook. Guardrail blocks are derived from synthetic tool-error messages (Phase 1) and upgrade cleanly to the planned waterfall middleware stage events (1.3.5i E3) when shipped. Empty-log sessions (path A/C calls) hide the tab rather than render an empty view; UI labels coverage boundaries so users aren't misled about replay semantics. Time-travel reuses the same `fold_session` path that live mutation uses, eliminating projection drift between replay and execution.

**Sandboxed browser automation (6.27)**: Agents drive a real headless Chromium (`browser_navigate/click/type/extract/screenshot/fill_form`) inside a dedicated Docker sandbox with per-environment domain allow-lists enforced fail-closed — navigation outside `allowedDomains` is refused before any network request leaves the container and upgrades the tool call to HIGH risk for approval. Framework-only competitors (LangGraph, CrewAI) leave browser tooling entirely to userland; SaaS competitors either omit it or run it outside the customer's trust boundary. Hecate's version inherits the platform's guardrail hooks, DLP scanning (text + screenshots), audit pipeline, and risk gating for free because it is a builtin tool on the standard `ToolRegistry` path.

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

Sources for the claims in this document (as of 2026-08-11):

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