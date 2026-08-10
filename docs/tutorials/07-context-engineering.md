# Tutorial: Context Engineering

> **25 minutes** — Make long-running agents stay coherent and on-budget. Configure the context pipeline, observe how messages flow through each stage, and use Context Offloading to preserve history across hundreds of turns.

The [context engineering pipeline](../concepts/context-engineering.md) runs before every LLM call. You usually do not interact with it directly — but when an agent in a long session starts forgetting, repeating itself, or hitting token limits, you need to know how to diagnose and tune it.

This tutorial walks through the practical side: what the pipeline does on each call, how to observe it, and how to configure offloading and budgets for your workload.

---

## What you will learn

- How to **observe** the context pipeline in action with traces
- How to **configure the token budget** for system prompt, history, evidence, and tools
- How to enable and tune **Context Offloading** so dropped messages are recoverable
- How to choose between **prioritization strategies** for message selection
- How to **debug** "the agent forgot what I said 30 turns ago"

## Prerequisites

- Hecate running locally — see [Quickstart](../getting-started/quickstart.md)
- An agent with a long conversation history (or use the seed script below)
- `hecate` CLI on your `PATH`
- Completed [Build Your First Agent](01-first-agent.md)

Throughout this tutorial we use `dev-key-change-me` as the API key.

---

## Step 1 — Generate a long conversation

To see the pipeline in action, you need a conversation long enough to exceed the model's context window. Use the seed script:

```bash
hecate agents create chat-assistant \
  --model gpt-4o-mini \
  --persona "You are a helpful assistant answering technical questions."
```

Then start a long session by sending 50+ messages of varied content:

```bash
hecate chat start chat-assistant --session long-conv-1
# Send a series of messages via /api/v1/chat/completions or the CLI
# Topic: ask 50 distinct questions about Python, web APIs, database design, etc.
```

After the session grows past ~50 turns, the context window will start filling. The LLMWorker pipeline kicks in.

---

## Step 2 — Observe the pipeline with traces

Every LLM call records a trace showing exactly what the pipeline did. Inspect a recent trace:

```bash
hecate traces list --session long-conv-1 --limit 1 --pretty
```

Look for the `context_pipeline` event in the trace payload. It contains a per-stage breakdown:

```json
{
  "context_pipeline": {
    "input_tokens": 92000,
    "budget": 32000,
    "stages": [
      {
        "name": "truncation",
        "before_tokens": 92000,
        "after_tokens": 71000,
        "tool_results_truncated": 3
      },
      {
        "name": "estimation",
        "tokens": 71000,
        "budget": 32000,
        "over_budget": true
      },
      {
        "name": "selection",
        "before_tokens": 71000,
        "after_tokens": 28000,
        "messages_kept": 18,
        "messages_dropped": 32,
        "strategy": "recency"
      },
      {
        "name": "offload",
        "enabled": true,
        "dropped_tokens": 38000,
        "files_written": 1,
        "stub_inserted": true
      },
      {
        "name": "compression",
        "skipped": true,
        "reason": "stub + selected within budget"
      }
    ],
    "final_tokens": 29500
  }
}
```

Read each stage to understand what happened:

- **truncation** — shortened oversized tool results so they fit within per-result limits
- **estimation** — measured token cost; `over_budget: true` triggered downstream stages
- **selection** — dropped 32 messages to fit the 32k budget (using the configured strategy)
- **offload** — wrote those 32 dropped messages to a JSON file in the environment; agent can retrieve via `read_file`
- **compression** — skipped because offloading reduced the live context enough

This is the pipeline in motion. The trace lets you see why your agent behaved a certain way on a given turn.

---

## Step 3 — Configure the token budget

The default budget allocates the context window roughly as 30% system prompt, 50% history, 10% evidence, 10% tools. You can override this per agent:

```bash
hecate agents update chat-assistant \
  --context-budget '{"system_prompt": 0.20, "history": 0.55, "evidence": 0.15, "tools": 0.10}'
```

The four allocations must sum to 1.0. The runtime interprets them as fractions of the model's context window. Tuning them is a workload-specific exercise:

| Workload | Suggested allocation | Rationale |
|----------|---------------------|-----------|
| Long conversations with simple Q&A | `system: 0.20, history: 0.65, evidence: 0.10, tools: 0.05` | Maximize history retention |
| Tool-heavy agents | `system: 0.15, history: 0.35, evidence: 0.15, tools: 0.35` | More budget for tool definitions |
| Research / RAG-heavy | `system: 0.20, history: 0.40, evidence: 0.30, tools: 0.10` | Maximize retrieved evidence |

After updating, run the same long conversation and compare the `context_pipeline` traces — the `selection` stage will retain more or fewer messages depending on your allocations.

---

## Step 4 — Enable Context Offloading

Offloading is **off by default** for backward compatibility. Enable it per agent or globally:

```bash
# Per-agent
hecate agents update chat-assistant \
  --context-offload-enabled true \
  --context-offload-threshold 8000

# Globally (set in .env)
echo "CONTEXT_OFFLOAD_ENABLED=true" >> .env
echo "CONTEXT_OFFLOAD_THRESHOLD_TOKENS=8000" >> .env
```

`threshold` controls the minimum number of dropped tokens before an offload is triggered. Setting it to 8000 means an offload file is only written when 8k+ tokens are about to be dropped — preventing trivial offloads for tiny overflows.

After enabling, look at the trace from Step 2 again. The `offload` stage should now show:

```json
{
  "name": "offload",
  "enabled": true,
  "dropped_tokens": 38000,
  "files_written": 1,
  "stub_inserted": true,
  "file_path": "memory/sessions/long-conv-1/offloaded_20260810T1530.json"
}
```

The dropped messages now live in the agent's environment filesystem. The agent can `read_file` the path to retrieve them.

---

## Step 5 — Verify the agent can recall dropped content

Send a follow-up message asking about something from much earlier in the conversation:

```bash
hecate chat send long-conv-1 \
  --message "Earlier I asked you about connection pooling. What was your recommendation again?"
```

If offloading is working, the agent can either:

- Read the offloaded file directly via `read_file memory/sessions/long-conv-1/offloaded_*.json` and quote its prior answer.
- Recognize the reference stub in its context and choose to retrieve.

Without offloading, the prior answer would be lost and the agent would have to guess or say "I don't have access to earlier messages."

Inspect the new trace to see whether the agent invoked `read_file` on the offloaded path. This is your verification that the loop is working.

---

## Step 6 — Choose a selection strategy

The `selection` stage uses a strategy to decide which messages to keep. Three strategies ship out of the box:

| Strategy | Behavior | Best for |
|----------|----------|----------|
| `recency` | Keep the most recent N messages that fit budget | Most conversations |
| `relevance` | Rank by semantic similarity to current user message | Q&A over long history |
| `hybrid` | Combine recency and relevance | Mixed workloads where some recent messages are filler |

Set the strategy per agent:

```bash
hecate agents update chat-assistant \
  --context-selection-strategy relevance
```

With `relevance`, messages that are semantically close to the current query are kept even if older. With `recency`, the last N turns are kept regardless of content. For most workloads, `recency` is the right default; switch to `relevance` when the agent needs to recall specific facts from many turns ago.

---

## Common patterns to debug

| Symptom | What to check |
|---------|---------------|
| Agent forgets what I said 30 turns ago | `selection.messages_dropped` is large; enable offloading or switch to `relevance` strategy |
| Agent invents facts that were never said | `compression` stage ran without offloading first; enable offloading so offload happens before compression |
| Agent loses access to a long tool result | `truncation.tool_results_truncated` is high; reduce `tool_result_limit` or split the tool output |
| Token-limit errors at the API call | `final_tokens` exceeds the model limit; reduce budget allocations or use a model with a larger window |
| Agent costs spike on long sessions | `evidence` allocation is too large — reduce and ensure prior messages get selected |

Each of these corresponds to a specific pipeline stage. Read the trace, identify the stage, and tune the corresponding setting.

---

## What you built

You now have a long-running agent that:

- Stays within budget as conversations grow
- Offloads dropped messages to the filesystem so old content is recoverable
- Uses a configurable selection strategy suited to the workload
- Records traceable evidence of every pipeline decision

The same configuration applies across all three execution modes (`chat`, `three_layer`, `workflow`). The pipeline runs before every LLM call regardless of mode.

---

## Further reading

- [Context Engineering](../concepts/context-engineering.md) — conceptual overview of the pipeline
- [Memory System](../concepts/memory.md) — the four-level memory architecture and how L2 compression relates
- [LLM Worker source](../../src/hecate/engine/workers/llm_worker.py) — the 5-step pipeline implementation
- [Context Offloader source](../../src/hecate/services/context/offloader.py) — the offload mechanism