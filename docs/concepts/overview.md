# Concepts

Hecate is an enterprise-grade, multi-tenant, model-agnostic Agent platform built around a self-developed Pregel execution engine. This section explains the core ideas you need to understand before building agents, configuring workflows, or operating a deployment.

If you prefer a hands-on introduction, start with the [Quickstart](../getting-started/quickstart.md) and the [tutorials](../tutorials/). For deep technical detail, see the [design documents](../design/).

---

## Topic articles

| Article | What it explains |
|---------|------------------|
| [Agents and Execution Modes](agents.md) | The Agent abstraction — persona, model, tools, knowledge, memory — and the three execution modes: `chat`, `three_layer`, and `workflow`. |
| [The Execution Engine](engine.md) | How the Pregel runtime turns a graph definition into executed supersteps using channels, workers, and checkpoints. |
| [Guardrails and Hooks](guardrails.md) | The four engine-level hook types that intercept every LLM and tool boundary, and how they power PII masking, audit logging, and human-in-the-loop. |
| [Context Engineering](context-engineering.md) | The pipeline that keeps long-running agents on-budget and on-task: assembly, evidence tracking, phase detection, token budgets, and prioritization. |
| [Multi-Tenancy](multi-tenancy.md) | The Organization → Workspace → User hierarchy and how `workspace_id` enforces data-level isolation across the platform. |
| [Memory System](memory.md) | The four-level memory architecture — working, conversation, user, and knowledge memory — and how each level persists agent state differently. |

---

## From here

- **Building an agent?** Read [Agents and Execution Modes](agents.md), then follow the [first agent tutorial](../tutorials/01-first-agent.md).
- **Designing a workflow?** Read [The Execution Engine](engine.md), then follow the [multi-agent tutorial](../tutorials/04-multi-agent.md).
- **Securing a deployment?** Read [Guardrails and Hooks](guardrails.md), then the [security architecture](../design/security-architecture.md).
- **Onboarding an organization?** Read [Multi-Tenancy](multi-tenancy.md), then the [SSO and SCIM guide](../how-to/configure-sso-scim.md).
