# Use Case: Research Team

> **45 minutes to build, 200-word summaries in ~30 seconds**

Three agents — a planner, a researcher, and a writer — collaborating on long-form research questions. Streams progress to the user, offloads context to keep costs down, and evaluates output quality against a ground-truth dataset. Combines **Multi-Agent + Streaming + Context engineering + Evaluation**.

---

## The scenario

A research analyst needs to write 5-10 brief reports per week. Each report:

- Takes a research question as input ("What are the latest advances in retrieval-augmented generation in 2026?")
- Decomposes into 3-5 sub-questions
- Searches the web for each
- Synthesizes a 200-word summary with citations
- Should complete in under 60 seconds

## The architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  User question                                                  │
│       │                                                         │
│       ▼                                                         │
│  ┌─────────────┐   sub-questions                                │
│  │   Planner   │ ─────────────────────┐                        │
│  │ (chat mode) │                      ▼                        │
│  └─────────────┘            ┌─────────────────┐                 │
│                              │   Researcher    │                 │
│                              │ (web_search +   │                 │
│                              │  context offload│                 │
│                              │  to long-term)  │                 │
│                              └────────┬────────┘                 │
│                                       │ findings + sources       │
│                                       ▼                         │
│                              ┌─────────────────┐                 │
│                              │     Writer      │                 │
│                              │  (200-word      │                 │
│                              │   summary +     │                 │
│                              │   citations)    │                 │
│                              └────────┬────────┘                 │
│                                       │                         │
│                                       ▼                         │
│                            Streamed to user (SSE)               │
└─────────────────────────────────────────────────────────────────┘
```

**Features combined**:

- [Multi-Agent Orchestration](../tutorials/04-multi-agent.md) — sequential 3-stage pipeline
- [OpenAI SDK Compatibility](../tutorials/10-openai-compatibility.md) — streaming via SSE
- [Context Engineering](../tutorials/07-context-engineering.md) — offload long research sessions
- [Evaluate an Agent](../tutorials/08-agent-evaluation.md) — quality scoring against expert-written summaries

---

## 10-line tldr

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="dev-key-change-me")

stream = client.chat.completions.create(
    model="workflow/research-team",
    messages=[{"role": "user", "content": "What are the latest advances in RAG in 2026?"}],
    stream=True,
    extra_body={"session_id": "research-2026-08-11-001"},
)

for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="", flush=True)
```

---

## The build

### Step 1 — Create the three agents

#### Planner

```bash
curl -X POST http://localhost:8000/api/agents \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Planner",
    "persona": "You decompose a research question into 3-5 specific sub-questions. Each sub-question must be: (1) searchable (verifiable facts), (2) narrow (answerable in 1-2 paragraphs), (3) non-overlapping. Output the sub-questions as a numbered list.",
    "model_config": {"model": "gpt-4o-mini", "temperature": 0.3}
  }'
```

#### Researcher (with web search + context offloading)

```bash
curl -X POST http://localhost:8000/api/agents \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Researcher",
    "persona": "For each sub-question you receive, search the web and synthesize findings with citations (URLs). If a finding exceeds 500 tokens, store it as a note in long-term memory and reference it by ID in your reply. Be skeptical: prefer primary sources over aggregators.",
    "model_config": {"model": "gpt-4o", "temperature": 0.2},
    "tools": ["web_search"],
    "memory_config": {
      "offload_threshold_tokens": 6000,
      "offload_target": "long_term",
      "long_term_namespace": "research-2026-q3"
    }
  }'
```

The `memory_config` enables Hecate's context offloading — when the researcher's context grows beyond 6,000 tokens, intermediate findings are moved to long-term memory and only summaries stay in the active context. This keeps costs flat even for long research sessions.

For context engineering details, see [Context Engineering](../tutorials/07-context-engineering.md).

#### Writer

```bash
curl -X POST http://localhost:8000/api/agents \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Writer",
    "persona": "You write a 200-word summary from the research findings. Rules: (1) every claim must be cited with a source URL, (2) one paragraph, no headings, no bullet points, (3) plain English (no jargon without explanation), (4) end with one sentence about open questions or limitations.",
    "model_config": {"model": "gpt-4o-mini", "temperature": 0.4}
  }'
```

Save each `id` as `PLANNER_ID`, `RESEARCHER_ID`, `WRITER_ID`.

### Step 2 — Create the workflow

```bash
cat > research-team-workflow.json <<EOF
{
  "name": "research-team",
  "entry": "planner",
  "nodes": [
    {
      "id": "planner",
      "type": "agent",
      "agent_id": "$PLANNER_ID"
    },
    {
      "id": "researcher",
      "type": "agent",
      "agent_id": "$RESEARCHER_ID",
      "input_mapping": {
        "sub_questions": "$nodes.planner.output.sub_questions"
      }
    },
    {
      "id": "writer",
      "type": "agent",
      "agent_id": "$WRITER_ID",
      "input_mapping": {
        "findings": "$nodes.researcher.output.findings"
      }
    }
  ],
  "edges": [
    {"from": "planner", "to": "researcher"},
    {"from": "researcher", "to": "writer"}
  ]
}
EOF

curl -X POST http://localhost:8000/api/workflows/import \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d @research-team-workflow.json
```

### Step 3 — Run it via streaming

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="dev-key-change-me",
)

stream = client.chat.completions.create(
    model="workflow/research-team",
    messages=[
        {"role": "user", "content": "What are the latest advances in retrieval-augmented generation in 2026?"},
    ],
    stream=True,                              # stream tokens as they arrive
    extra_body={"session_id": "research-001"},  # checkpoint-resumable
)

for chunk in stream:
    delta = chunk.choices[0].delta.content
    if delta:
        print(delta, end="", flush=True)
print()
```

Output streams in ~30 seconds (planner: 2s, researcher: 20s for 3 sub-questions, writer: 5s). Total cost: ~$0.04 per report (gpt-4o for research + gpt-4o-mini for planner/writer).

For more on streaming, see [OpenAI SDK Compatibility](../tutorials/10-openai-compatibility.md) Step 3.

### Step 4 — Make it resumable

Long research sessions can crash mid-flight. Use the `session_id` parameter — Hecate persists state at each step, so you can resume from the last checkpoint:

```python
# First attempt — got cut off
stream = client.chat.completions.create(
    model="workflow/research-team",
    messages=[{"role": "user", "content": "..."}],
    stream=True,
    extra_body={"session_id": "research-001"},
)

# ... user closed the browser ...

# Resume from last checkpoint
result = client.chat.completions.create(
    model="workflow/research-team",
    messages=[{"role": "user", "content": "continue"}],  # any message — context restored from session
    extra_body={"session_id": "research-001"},
)
```

The researcher picks up where it left off — including any web search results already gathered.

---

## The evaluation

Three metrics:

### 1. **Quality vs expert-written summaries**

```bash
hecate eval run \
  --dataset research-quality-v1 \
  --evaluator llm-judge \
  --judge-model "gpt-4o" \
  --workflow research-team
```

`research-quality-v1` is a held-out set of 20 expert-written summaries on research questions. The judge LLM scores each generated summary 1-5 on accuracy, completeness, and citation quality. Target: **average score ≥ 4.0**.

### 2. **Citation coverage**

```bash
hecate eval run --dataset research-quality-v1 --evaluator citation-coverage --workflow research-team
```

Target: **100%** of factual claims have at least one source URL.

### 3. **End-to-end latency**

```bash
hecate eval run --dataset research-quality-v1 --evaluator latency --workflow research-team
```

Target: **p95 < 60 seconds** per research question.

For evaluation patterns, see [Evaluate an Agent](../tutorials/08-agent-evaluation.md).

---

## Adapt it

| Variation | Change |
|---|---|
| **Domain-specific** | Add a domain knowledge base (e.g., legal cases, medical literature) to the researcher's `knowledge_base_ids` |
| **Multi-source** | Add 3 specialized researchers (academic, news, blog) and have the planner route sub-questions to the right one |
| **Citation audit** | Add a 4th agent that verifies each citation actually supports the claim it cites |
| **Cost optimization** | Use gpt-4o-mini everywhere except for the planner; offload aggressively to keep costs under $0.02/report |
| **Weekly digest** | Wrap the workflow in a cron-scheduled trigger that runs every Monday and emails results |

---

## When NOT to use this pattern

- **Time-sensitive questions** — web search latency + 3-stage pipeline is too slow for "what's the stock price"
- **Single-source topics** — if all info is in one place, skip the planner/researcher and just retrieve-and-summarize
- **High-stakes domains** — medical/legal research needs expert review; use this as a draft tool, not a final answer

---

## What's next

- **[Customer Support Bot](01-customer-support-bot.md)** — RAG + guardrails + HITL
- **[Code Review Agent](02-code-review-agent.md)** — multi-agent + MCP
- **[Multi-Agent Orchestration deep dive](../tutorials/04-multi-agent.md)**
- **[Context Engineering](../tutorials/07-context-engineering.md)** — the offloading mechanism explained