# Use Cases

End-to-end business scenarios built on Hecate. Each use case combines multiple Hecate features into a working example you can adapt.

Use cases are **recipes**, not feature tutorials. For deep dives on individual features, see the [Tutorials](../tutorials/README.md).

---

## Available use cases

### 1. **[Customer support bot](01-customer-support-bot.md)** *(30 min)*
A bot that answers from your documentation, escalates uncertain questions to a human, and never leaks PII.
- **Features**: RAG (knowledge base) + Guardrails (PII masking) + Human-in-the-Loop + MCP (Zendesk/Slack ticket creation)
- **Outcome**: 70% of support tickets answered automatically; 30% escalated with full context

### 2. **[Code review agent](02-code-review-agent.md)** *(30 min)*
A multi-agent system that reviews PRs for security, style, and correctness — and only flags issues a human reviewer would actually care about.
- **Features**: MCP (GitHub filesystem/PR tools) + Multi-Agent (3 parallel reviewers + 1 aggregator) + Evaluation (regression-tested prompt templates)
- **Outcome**: Reviewer-load drops 40%; PR turnaround halved

### 3. **[Research team](03-research-team.md)** *(45 min)*
Three agents — a planner, a researcher, and a writer — collaborating on long-form research questions, with streaming progress and evaluation against a ground-truth dataset.
- **Features**: Multi-Agent orchestration + Streaming + Context engineering (offloading for long sessions) + Evaluation
- **Outcome**: 200-word summaries in ~30 seconds; quality scores match expert-written summaries within 5%

---

## How to use these

Each use case is structured the same way:

1. **The scenario** — who uses it and what problem it solves
2. **The architecture** — which Hecate features combine, with a diagram
3. **The build** — concrete steps to reproduce (CLI + API + JSON snippets)
4. **The evaluation** — how to measure if the use case is working
5. **Adapt it** — common variations and customization points

## When to write a new use case

Use cases belong here (not in `tutorials/`) when they:

- Solve a **recognizable business problem** with a name (customer support, code review, etc.)
- Combine **three or more** Hecate features
- Have a **measurable outcome** (cost, latency, quality)

If your scenario is just a feature demo (e.g., "show me how to use streaming"), it belongs in [Tutorials](../tutorials/README.md).

## Conventions

- Each use case has a "10-line tldr" at the top — a code or JSON snippet that captures the entire solution
- Realistic UUIDs and timestamps (no `xxxx`)
- Evaluation section with concrete metrics, not vague "it works well"
- "Adapt it" section listing the 3-5 most common modifications