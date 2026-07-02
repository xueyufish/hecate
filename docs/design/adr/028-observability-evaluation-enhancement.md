# ADR-028: Observability & Evaluation Enhancement

## Status

Accepted

## Context

Competitive analysis of 10+ platforms (LangSmith, Langfuse, Braintrust, Palantir AIP, Salesforce Agentforce, IBM watsonx.governance, Google Vertex AI, Dify, OpenTelemetry GenAI, and industry best practices) revealed gaps in Hecate's Ops Center observability and evaluation capabilities. While Hecate has a strong foundation (40+ evaluators, full-chain tracing, unified dashboard, testing center), several production-grade capabilities are missing.

The analysis identified 6 gaps across observability and evaluation:

1. **CI/CD Evaluation Gating** (OE1) — Braintrust and LangSmith integrate evaluation results with deployment pipelines; evaluation score regression blocks deployment automatically. Hecate's Testing Center (7.9) runs evaluations but does not gate deployments.

2. **OTel GenAI Semantic Conventions** (OE2) — OpenTelemetry's GenAI semantic conventions (2025-2026) define standardized Span attributes (`gen_ai.operation.name`, `gen_ai.request.model`, `gen_ai.agent.id`, etc.). Hecate uses OTel context propagation but does not align with GenAI-specific attributes.

3. **Production Online Scoring** (OE3) — Braintrust and Langfuse support real-time sampling of production traffic with LLM-as-Judge auto-scoring. Hecate has online/offline evaluation tasks (7.2c) but lacks production traffic scoring.

4. **Data-to-Decision Traceability** (OE4) — Palantir AIP provides ontology-level decision lineage: data source version → transformation logic → model version → prompt version → decision result → feedback. Hecate's Decision Lineage (6.21) records who/when/what but lacks the full provenance chain.

5. **Topic Clustering & Low-Score Analysis** (OE5) — Salesforce Agentforce Optimization auto-clusters conversations by topic and identifies systematically low-scoring clusters. Hecate's Conversation Analytics (8.9b) has topic distribution but lacks systematic low-score analysis.

6. **Multi-Agent Distributed Tracing** (OE6) — OTel GenAI agent spans and A2A protocol enable end-to-end tracing across agent-to-agent calls. Hecate has W3C Trace Context Propagation (8.1d) but lacks sub-agent execution visualization.

Additionally, two capabilities were identified as new features:

7. **Agent Catalog Governance & Quality Gateway** (OE7) — IBM watsonx.governance and Salesforce require quality evaluation before agent publishing. Hecate has no pre-publish quality gate.

8. **Evaluation Metrics Structuring** (OE8) — Industry practice organizes evaluation into Effectiveness/Efficiency/Safety dimensions. Hecate's 40+ evaluators are a flat list without structured grouping.

9. **Reasoning Efficiency Evaluator** (OE9) — Evaluating agent reasoning efficiency (superstep count, tool call frequency) is a novel evaluation dimension not present in Hecate.

10. **Adversarial Test Generation** (OE10) — IBM watsonx.governance's red teaming generates adversarial samples automatically. Hecate's Automated Continuous Red Teaming (7.10) has fixed test sets without auto-generation.

## Decision

Implement 2 new features and 8 enhancements across the Ops Center:

### New Features

1. **CI/CD Evaluation Gating (8.10)** — Integrate evaluation results with CI/CD pipeline. Evaluation score regression automatically blocks deployment. Git PR-triggered evaluation with baseline comparison. Configurable regression tolerance thresholds per evaluator. Integrates with Testing Center (7.9) and Regression Test Set (7.6).

2. **Agent Catalog Governance & Quality Gateway (8.12)** — Agent registration to managed catalog requires quality evaluation gateway. Pre-publish quality assessment: journey completion rate, tool call accuracy, instruction adherence, answer relevancy, safety metrics. Quality score computation with configurable pass thresholds. Only quality-compliant agents can be published.

### Enhancements

3. **OTel GenAI Semantic Conventions Alignment (OE2)** — Standardize Span attributes to OpenTelemetry GenAI semantic conventions (`gen_ai.operation.name`, `gen_ai.request.model`, `gen_ai.agent.id`, `gen_ai.usage.total_tokens`, `gen_ai.tool.name`). Ensures seamless interoperability with OTel-compatible backends.

4. **Production Online Scoring (OE3)** — Real-time sampling of production traffic with LLM-as-Judge auto-scoring. Continuous quality monitoring without waiting for offline batch runs. Integrates with Online/Offline Evaluation Tasks (7.2c).

5. **Data-to-Decision Full-Chain Traceability (OE4)** — Extend Decision Lineage (6.21) to ontology-level: Data Source Version → Transformation Logic → Model Version → Prompt Version → Decision Result → Feedback. Every link versioned and traceable.

6. **Conversation Topic Clustering & Systematic Low-Score Analysis (OE5)** — Auto-cluster conversations by topic, identify systematically low-scoring topic clusters, generate targeted improvement suggestions. Extends Conversation Analytics (8.9b).

7. **Multi-Agent Distributed Tracing (OE6)** — End-to-end tracing across A2A agent calls with sub-agent execution visualization, cross-organization trace correlation. Extends W3C Trace Context Propagation (8.1d).

8. **Evaluation Metrics Three-Dimension Structuring (OE8)** — Organize evaluators into Effectiveness (task completion, answer accuracy, RAG quality), Efficiency (reasoning steps, tool call frequency, per-task cost), and Safety (content violation rate, unauthorized operations, jailbreak pass rate) dimensions.

9. **Reasoning Efficiency Evaluator (OE9)** — New evaluator based on Pregel superstep count (reasoning rounds) and Tool Call Span count (tool invocation frequency). Detects decision redundancy and unnecessary tool calls.

10. **Adversarial Test Generation (OE10)** — LLM auto-generates boundary cases and adversarial samples to supplement evaluation datasets. Auto-verifies agent robustness before publishing. Extends Automated Continuous Red Teaming (7.10).

## Rationale

- **OE1 (CI/CD Gating)**: Braintrust's deployment-blocking workflow is the most production-oriented evaluation story in 2026. Without this, evaluation results are advisory, not enforceable.
- **OE2 (OTel GenAI)**: OpenTelemetry GenAI semantic conventions are stabilizing (2025-2026). Early alignment avoids migration cost and enables interoperability with the growing OTel ecosystem.
- **OE3 (Online Scoring)**: Offline evaluation catches regressions in batches; online scoring catches them in real-time. Both are needed.
- **OE4 (Data-to-Decision)**: Palantir's ontology-level observability is a key differentiator. Extending Decision Lineage to full provenance matches this capability.
- **OE5 (Topic Clustering)**: Identifying which topics systematically underperform enables targeted improvement rather than global optimization.
- **OE6 (Multi-Agent Tracing)**: A2A adoption means multi-agent workflows are production reality. End-to-end tracing is essential for debugging.
- **OE7 (Quality Gateway)**: IBM watsonx.governance's governed agent catalog ensures only quality-assured agents reach production. This is enterprise governance 101.
- **OE8 (Three Dimensions)**: Flat evaluator lists are hard to navigate. Structured dimensions align with how operators think about quality.
- **OE9 (Reasoning Efficiency)**: Superstep count and tool call frequency are Hecate-specific metrics that reveal agent efficiency problems invisible to generic evaluators.
- **OE10 (Adversarial Generation)**: Fixed test sets become stale. LLM-generated adversarial samples keep evaluation fresh and cover edge cases humans miss.

## Consequences

- Feature count increases from 341 to 343 (2 new features)
- 10 changes total (2 new features + 8 enhancements) across Ops Center
- OTel GenAI alignment requires updating existing Span attribute naming conventions
- CI/CD evaluation gating requires integration with deployment pipeline (not just Testing Center)
- Agent Catalog Governance requires a new quality assessment workflow before agent publishing
- Multi-Agent Distributed Tracing depends on A2A protocol adoption in production

## Related Documents

- [Ops Center Design](../ops-center-design.md) — Design document for all OE enhancements
- [Feature Catalog](../../features/feature-catalog.md) — 343 features including OE1-OE10
- [Roadmap](../../features/roadmap.md) — Sprint 7: Observability & Evaluation Enhancement
- [ADR-021: Ops Center Architecture](021-ops-center-architecture.md) — Ops Center composition architecture
- [ADR-008: Security via Hooks](008-security-via-hooks.md) — Guardrail hooks for evaluation enforcement
