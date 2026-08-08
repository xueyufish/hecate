# Tutorial: Multi-Agent Orchestration

> Documentation in progress. This tutorial will cover building workflows with Hecate's six multi-agent collaboration patterns.

## What you will build

A multi-agent workflow that coordinates several specialized agents to handle a complex task.

## Prerequisites

- Hecate running locally (see [Quickstart](../getting-started/quickstart.md))
- At least two agents created (see [Quickstart Step 7](../getting-started/quickstart.md#step-7--create-your-first-agent))

## Collaboration patterns

Hecate supports six patterns, all unified as Graph templates:

| Pattern | When to use |
|---------|-------------|
| **Hierarchical** | A supervisor agent delegates to sub-agents |
| **Handoff** | One agent transfers control to another (with context modes: inherited, isolated, summarized) |
| **Pipeline** | Agents execute in sequence, each processing the previous agent's output |
| **Broadcast** | A message is sent to multiple agents in parallel |
| **Negotiation** | Agents propose and counter-propose until consensus |
| **Debate** | Agents argue different positions; a judge agent selects the best answer |

## Steps (outline)

1. Create specialist agents (each with its own persona, tools, and knowledge bases)
2. Define a workflow graph in JSON DSL or via the visual canvas
3. Compile and validate the graph
4. Execute the workflow via the chat endpoint or async execution API
5. Inspect execution traces and checkpoints

## Further reading

- [Engine Design: Multi-Agent Handoff](../design/engine-design.md#multi-agent-handoff)
- [Agent Studio Design](../design/agent-studio-design.md)