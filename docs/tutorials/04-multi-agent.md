# Tutorial: Multi-Agent Orchestration

> **25 minutes** — Build agent workflows with Hecate's six collaboration patterns. Use prebuilt templates, generate graphs from pattern parameters, attach workflows to agents, and run them end-to-end.

Multi-agent orchestration is Hecate's flagship differentiator. A workflow is a graph of nodes (agents, conditions, fan-outs, merges) connected by edges (data flow, control flow, handoff triggers). Hecate's Pregel runtime executes these graphs as supersteps, with a durable event log (Log-as-Truth), retries, and tracing.

---

## What you will learn

- The difference between **agent modes**: `chat`, `three_layer`, and `workflow`
- The **six collaboration patterns**: sequential, parallel, handoff, broadcast, negotiation, debate
- How to **use a prebuilt template** (`/api/orchestration-templates`)
- How to **generate a graph** from a pattern (`/api/collaboration-patterns/{pattern}/generate`)
- How to **create, version, and publish** a workflow
- How to **attach a workflow to an agent** and run it via chat
- How to **inspect execution traces** for debugging

## Prerequisites

- Hecate running locally — see [Quickstart](../getting-started/quickstart.md)
- At least one LLM provider configured in `.env`
- `hecate` CLI on your `PATH`
- Completed [Build Your First Agent](01-first-agent.md)

Throughout this tutorial we use `dev-key-change-me` as the API key. Replace it with your actual `HECATE_API_KEYS` value.

---

## Step 1 — Choose the right execution mode

Every agent has a `mode` field that controls how chat requests are processed:

| Mode | What runs | When to use |
|------|-----------|-------------|
| **`chat`** | A single LLM call with optional tools and KB retrieval | Direct Q&A, single-task automation |
| **`three_layer`** | Guard → Planner → Sub-Agent pipeline | Complex single-agent tasks that need planning and validation |
| **`workflow`** | A custom graph compiled from the workflow graph DSL | Multi-agent coordination, structured decision flows |

This tutorial is about `workflow` mode — the most powerful option. The other two modes are covered in [Tutorial 01](01-first-agent.md) and the [engine design docs](../design/engine-design.md).

---

## Step 2 — Understand the six collaboration patterns

Hecate unifies all multi-agent topologies under six collaboration patterns, each generated from a high-level configuration and emitted as a graph DSL:

| Pattern | Topology | When to use |
|---------|----------|-------------|
| **Sequential** | Stages execute one after another; each stage's output feeds the next | Document processing pipelines, multi-step transformations |
| **Parallel** | A coordinator fans out to N workers; an aggregator collects results | Research tasks, multi-perspective analysis |
| **Handoff** | A router agent routes to one of N specialists based on intent | Customer service triage, domain-specific delegation |
| **Broadcast** | All participants share a topic channel; messages flow round-robin | Group discussion, multi-stakeholder review |
| **Negotiation** | Two agents propose and counter-propose until agreement | Conflict resolution, contract drafting |
| **Debate** | Two debaters argue opposing views; a judge selects the best answer | Decision support, exploring tradeoffs |

The patterns live in `src/hecate/engine/patterns.py`. Each has a builder in `engine/templates.py` that emits a `GraphConfig` — nodes, edges, channels — ready for compilation.

---

## Step 3 — Browse prebuilt orchestration templates

Hecate ships 10 ready-to-use templates under `src/hecate/data/orchestration_templates/`:

```bash
curl -H "Authorization: Bearer dev-key-change-me" \
  http://localhost:8000/api/orchestration-templates | jq '.items[] | {id, name, category}'
```

```json
{"id": "broadcast-pipeline", "name": "Broadcast Pipeline", "category": "broadcast"}
{"id": "conditional-pipeline", "name": "Conditional Pipeline", "category": "sequential"}
{"id": "content-pipeline", "name": "Content Pipeline", "category": "sequential"}
{"id": "customer-service-triage", "name": "Customer Service Triage", "category": "handoff"}
{"id": "debate", "name": "Debate", "category": "debate"}
{"id": "fan-out-pipeline", "name": "Fan-out Pipeline", "category": "parallel"}
{"id": "hierarchical-supervisor", "name": "Hierarchical Supervisor", "category": "parallel"}
{"id": "negotiation", "name": "Negotiation", "category": "negotiation"}
{"id": "reflection-loop", "name": "Reflection Loop", "category": "sequential"}
{"id": "sequential-pipeline", "name": "Sequential Pipeline", "category": "sequential"}
```

Fetch the full graph DSL for any template:

```bash
curl -H "Authorization: Bearer dev-key-change-me" \
  http://localhost:8000/api/orchestration-templates/customer-service-triage | jq
```

The response includes the complete graph DSL — `nodes`, `edges`, `state` channels — ready to import as a workflow or use as a starting point.

---

## Step 4 — Generate a graph from a pattern (the easy way)

If you don't want to write graph DSL by hand, ask Hecate to generate one from a pattern and parameters:

```bash
curl -X POST http://localhost:8000/api/collaboration-patterns/handoff/generate \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "config": {
      "router": {
        "model": "gpt-4o-mini",
        "system_prompt": "You are a triage router. Classify the user request into one of: billing, technical, account. Reply with just the category name."
      },
      "specialists": [
        {
          "name": "billing",
          "model": "gpt-4o-mini",
          "system_prompt": "You handle billing questions — invoices, refunds, subscription changes."
        },
        {
          "name": "technical",
          "model": "gpt-4o-mini",
          "system_prompt": "You handle technical issues — bugs, errors, integration problems."
        },
        {
          "name": "account",
          "model": "gpt-4o-mini",
          "system_prompt": "You handle account questions — login, profile, permissions."
        }
      ]
    }
  }' | jq
```

The response is a complete graph DSL with router node, three specialist nodes, handoff edges, and routing logic. Save it to a file:

```bash
curl ... | jq '.graph_dsl' > triage-workflow.json
```

### Required config per pattern

| Pattern | Required parameters |
|---------|---------------------|
| `sequential` | `stages[]` — each stage with `name`, `model`, `system_prompt` |
| `parallel` | `coordinator`, `workers[]`, `aggregator` — each with `model`, optional `system_prompt` |
| `handoff` | `router`, `specialists[]` — each with `model`, `system_prompt` |
| `broadcast` | `participants[]` — each with `name`, `model`, `system_prompt`, optional `moderator` |
| `negotiation` | `proposer`, `responder` — each with `model`, `system_prompt`; optional `max_rounds` |
| `debate` | `debater_a`, `debater_b` — each with `model`, `system_prompt`; optional `judge`, `rounds` |

---

## Step 5 — Create the specialist agents

Patterns reference agent IDs, so you need real agents first. Create three specialists for the handoff example:

```bash
for persona in "billing" "technical" "account"; do
  case $persona in
    billing) desc="You handle billing questions — invoices, refunds, subscription changes.";;
    technical) desc="You handle technical issues — bugs, errors, integration problems.";;
    account) desc="You handle account questions — login, profile, permissions.";;
  esac

  hecate agent create \
    --name "Triage: $persona Specialist" \
    --model "gpt-4o-mini" \
    --mode chat \
    --persona "$desc"
done
```

Capture the three IDs:

```bash
BILLING_ID=$(hecate agent list --json | jq -r '.items[] | select(.name | contains("billing")) | .id')
TECHNICAL_ID=$(hecate agent list --json | jq -r '.items[] | select(.name | contains("technical")) | .id')
ACCOUNT_ID=$(hecate agent list --json | jq -r '.items[] | select(.name | contains("account")) | .id')
echo "Billing:   $BILLING_ID"
echo "Technical: $TECHNICAL_ID"
echo "Account:   $ACCOUNT_ID"
```

### Patch the generated graph DSL

The generator emitted placeholder agent IDs. Replace them with your real agent IDs:

```bash
jq --arg b "$BILLING_ID" --arg t "$TECHNICAL_ID" --arg a "$ACCOUNT_ID" \
  '.nodes["billing-specialist"].config.agent_id = $b |
   .nodes["technical-specialist"].config.agent_id = $t |
   .nodes["account-specialist"].config.agent_id = $a' \
   triage-workflow.json > triage-workflow-patched.json
```

> **Inspect the structure first.** The exact node names depend on what the generator emitted. Use `jq '.nodes | keys'` to list node IDs before patching.

---

## Step 6 — Create and publish a workflow

A workflow is a versioned graph definition. Create one from your patched DSL:

```bash
WORKFLOW_DSL=$(cat triage-workflow-patched.json)

curl -X POST http://localhost:8000/api/workflows \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"Customer Service Triage\",
    \"description\": \"Routes customer requests to specialized agents\",
    \"graph_dsl\": $WORKFLOW_DSL
  }"
```

Save the workflow `id` from the response. Workflows start at version 1 and can be updated later as version 2, 3, etc.

### Validate the graph

Before publishing, validate the graph compiles without errors:

```bash
curl -X POST http://localhost:8000/api/workflows/<WORKFLOW_ID>/validate \
  -H "Authorization: Bearer dev-key-change-me" -d '{}' -H "Content-Type: application/json"
```

Returns `{ "valid": true, "errors": [] }` on success.

### Publish version 1

Publishing makes a version "live" — the version agents reference when running in `workflow` mode:

```bash
curl -X POST http://localhost:8000/api/workflows/<WORKFLOW_ID>/publish/1 \
  -H "Authorization: Bearer dev-key-change-me"
```

---

## Step 7 — Test the workflow

Run a test execution before attaching to an agent:

```bash
curl -X POST http://localhost:8000/api/workflows/<WORKFLOW_ID>/test-run \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "input": {
      "messages": [{"role": "user", "content": "My invoice for last month is wrong. Can you fix it?"}]
    }
  }'
```

Returns the run id and final state. Inspect past runs:

```bash
curl -H "Authorization: Bearer dev-key-change-me" \
  http://localhost:8000/api/workflows/<WORKFLOW_ID>/runs
```

---

## Step 8 — Attach the workflow to an agent

Create a workflow-mode agent that uses this graph:

```bash
curl -X POST http://localhost:8000/api/agents \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Customer Service Workflow Agent",
    "persona": "You are a customer service dispatcher.",
    "model_config": {"model": "gpt-4o-mini"},
    "mode": "workflow",
    "workflow_id": "<WORKFLOW_ID>"
  }'
```

Save the agent's `id`. Chat requests addressed to this agent now flow through the compiled graph — the router classifies the request, hands off to the matching specialist, and the specialist's response becomes the final answer.

---

## Step 9 — Run the workflow via chat

Same `/v1/chat/completions` endpoint you've used in earlier tutorials — just point at the workflow agent:

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agent/<WORKFLOW_AGENT_ID>",
    "messages": [
      {"role": "user", "content": "I cannot log in to my account since this morning."}
    ]
  }'
```

The router classifies this as "account" and hands off to the account specialist. The response reflects the specialist's persona.

For a different intent:

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agent/<WORKFLOW_AGENT_ID>",
    "messages": [
      {"role": "user", "content": "Your API is throwing 500 errors intermittently."}
    ]
  }'
```

The router routes to "technical" this time. Same workflow, different specialist.

---

## Step 10 — Inspect execution traces

Every workflow run produces traces. Use them to debug routing decisions or handoff behavior:

```bash
curl -H "Authorization: Bearer dev-key-change-me" \
  "http://localhost:8000/api/traces?agent_id=<WORKFLOW_AGENT_ID>&limit=5"
```

Each trace contains the full superstep sequence — which node ran, what input it received, what output it produced, and the timing. Useful for verifying that the router classified correctly and that handoff edges fired as expected.

---

## Pattern recipes

### Sequential (Pipeline)

For ETL-like tasks: parse, transform, validate, store. Each stage is an agent with a clear input/output contract.

```bash
curl -X POST http://localhost:8000/api/collaboration-patterns/sequential/generate \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "config": {
      "stages": [
        {"name": "extract", "model": "gpt-4o-mini", "system_prompt": "Extract structured data from raw text."},
        {"name": "transform", "model": "gpt-4o-mini", "system_prompt": "Normalize the extracted data."},
        {"name": "validate", "model": "gpt-4o-mini", "system_prompt": "Check the data for completeness and consistency."}
      ]
    }
  }'
```

### Parallel (Fan-out + Aggregator)

For research tasks: one coordinator frames the question, N workers research in parallel, an aggregator synthesizes.

```bash
curl -X POST http://localhost:8000/api/collaboration-patterns/parallel/generate \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "config": {
      "coordinator": {"model": "gpt-4o-mini", "system_prompt": "Frame the research question."},
      "workers": [
        {"model": "gpt-4o-mini", "system_prompt": "Research technical implications."},
        {"model": "gpt-4o-mini", "system_prompt": "Research business implications."},
        {"model": "gpt-4o-mini", "system_prompt": "Research user-impact implications."}
      ],
      "aggregator": {"model": "gpt-4o-mini", "system_prompt": "Synthesize the three perspectives into a coherent answer."}
    }
  }'
```

### Negotiation

For two-party discussions that need consensus. The proposer and responder alternate up to `max_rounds` times; loop ends when `agreement_status` flips to "agreed".

```bash
curl -X POST http://localhost:8000/api/collaboration-patterns/negotiation/generate \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "config": {
      "proposer": {"model": "gpt-4o-mini", "system_prompt": "You propose contract terms. Aim for win-win."},
      "responder": {"model": "gpt-4o-mini", "system_prompt": "You respond to proposals. Accept reasonable offers, counter unreasonable ones."},
      "max_rounds": 5
    }
  }'
```

### Debate

For decisions where you want opposing viewpoints explored. Two debaters argue, a judge picks the best argument.

```bash
curl -X POST http://localhost:8000/api/collaboration-patterns/debate/generate \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "config": {
      "debater_a": {"model": "gpt-4o-mini", "system_prompt": "You argue FOR the proposal."},
      "debater_b": {"model": "gpt-4o-mini", "system_prompt": "You argue AGAINST the proposal."},
      "judge": {"model": "gpt-4o-mini", "system_prompt": "You are an impartial judge. Evaluate both arguments and select the stronger position."},
      "rounds": 3
    }
  }'
```

### Broadcast

For group discussions where multiple stakeholders need to weigh in.

```bash
curl -X POST http://localhost:8000/api/collaboration-patterns/broadcast/generate \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "config": {
      "participants": [
        {"name": "alice", "model": "gpt-4o-mini", "system_prompt": "You are Alice, the engineer."},
        {"name": "bob", "model": "gpt-4o-mini", "system_prompt": "You are Bob, the product manager."},
        {"name": "carol", "model": "gpt-4o-mini", "system_prompt": "You are Carol, the customer success lead."}
      ],
      "moderator": {"model": "gpt-4o-mini", "system_prompt": "You are the moderator. Frame the question and summarize at the end."}
    }
  }'
```

---

## Versioning and updates

Workflows are versioned. Update the graph to create version 2; publish it when ready; agents referencing the workflow automatically pick up the new version:

```bash
# Update creates version 2
curl -X PUT http://localhost:8000/api/workflows/<WORKFLOW_ID> \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d "{\"graph_dsl\": $(cat updated-workflow.json)}"

# Validate before publishing
curl -X POST http://localhost:8000/api/workflows/<WORKFLOW_ID>/validate \
  -H "Authorization: Bearer dev-key-change-me"

# Publish version 2 — agents now use the new graph
curl -X POST http://localhost:8000/api/workflows/<WORKFLOW_ID>/publish/2 \
  -H "Authorization: Bearer dev-key-change-me"
```

Compare versions:

```bash
curl -H "Authorization: Bearer dev-key-change-me" \
  http://localhost:8000/api/workflows/<WORKFLOW_ID>/diff?from=1&to=2
```

Roll back to a previous version:

```bash
curl -X POST http://localhost:8000/api/workflows/<WORKFLOW_ID>/rollback/1 \
  -H "Authorization: Bearer dev-key-change-me"
```

---

## Writing your own graph DSL

For patterns the generators don't cover, write the DSL directly. The schema is `src/hecate/engine/graph-dsl.schema.json`. A minimal example:

```json
{
  "version": "1.0",
  "name": "Custom Loop",
  "state": {
    "messages": { "type": "topic", "reduce": "append" }
  },
  "nodes": {
    "writer": {
      "type": "agent",
      "config": {
        "agent_id": "<uuid>",
        "system_prompt": "You draft content.",
        "channels": { "readable": ["messages"], "writable": ["messages"] }
      }
    },
    "critic": {
      "type": "agent",
      "config": {
        "agent_id": "<uuid>",
        "system_prompt": "You critique drafts. If acceptable, reply APPROVED; otherwise suggest improvements.",
        "channels": { "readable": ["messages"], "writable": ["messages"] }
      }
    },
    "exit_check": {
      "type": "condition",
      "config": {
        "expression": "'APPROVED' in state.messages[-1].content"
      }
    }
  },
  "edges": [
    {"from": "writer", "to": "critic"},
    {"from": "critic", "to": "exit_check"},
    {"from": "exit_check", "to": "writer", "when": "false"},
    {"from": "exit_check", "to": "__end__", "when": "true"}
  ]
}
```

Node types: `agent`, `condition`, `fan_out`, `merge`. Channel types: `topic` (append-reduce), `last_value`. Edges can have `when` predicates for conditional routing.

See the bundled [Graph DSL JSON Schema](../../src/hecate/engine/graph-dsl.schema.json) for the full schema definition.

---

## Troubleshooting

### `validate` returns errors

Read the error list — common causes:

- **Unknown node type** — only `agent`, `condition`, `fan_out`, `merge` are valid
- **Cycle without exit** — every loop must have a `condition` node with a path to `__end__`
- **Missing channels** — `agent` nodes must declare `readable` and `writable` channels
- **Bad edge references** — `from`/`to` must reference existing node IDs or `__start__`/`__end__`

### Generator returns 422

The config is missing a required parameter for the chosen pattern. See the pattern parameter table in Step 4.

### Workflow run hangs

A loop's exit condition never evaluates to `true`. Check the `condition` node's `expression` — it must reference state that actually changes during execution.

### Handoff routes everything to the same specialist

The router's system prompt isn't discriminative enough. Tighten it: "Reply with exactly one of: billing, technical, account. Nothing else."

### "Workflow not published"

The agent references a workflow that has no published version. Run `publish/1` (or whichever version you want).

### Trace shows the right intent but the wrong specialist ran

The router and specialists share state channels incorrectly. Check that each specialist reads from the same channel the router writes to, and not from another specialist's output channel.

---

## Summary

You now know how to:

- **Pick the right execution mode** — `chat`, `three_layer`, or `workflow`
- **Use the six collaboration patterns** — sequential, parallel, handoff, broadcast, negotiation, debate
- **Browse prebuilt templates** at `/api/orchestration-templates`
- **Generate graphs** from high-level config via `/api/collaboration-patterns/{pattern}/generate`
- **Create, version, validate, and publish** workflows
- **Attach workflows to agents** and run them via the standard chat endpoint
- **Inspect execution traces** for debugging

## Next steps

- **[MCP Tool Integration](03-mcp-integration.md)** — connect Hecate as an MCP client to external tool servers, or expose it as an MCP server itself.
- **[Engine Design](../design/engine-design.md#multi-agent-handoff)** — deep dive into how the Pregel runtime executes multi-agent handoff graphs.
- **[Agent Studio Design](../design/agent-studio-design.md)** — visual canvas for designing workflows without writing JSON.
- **[Tutorial: Knowledge Base and RAG](02-knowledge-base.md)** — attach knowledge bases to specialist agents for domain expertise.