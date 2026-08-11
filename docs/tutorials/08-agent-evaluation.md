# Evaluate an Agent

Build an evaluation dataset, run built-in evaluators over your agent's answers, read the scores, and use the results to iterate without guesswork.

This tutorial assumes you have completed [Build Your First Agent](01-first-agent.md) and have an agent ID and a running Hecate server (`http://localhost:8000`). Set your API key and agent ID as shell variables:

```bash
export HECATE_API_KEY=dev-key-change-me
export AGENT_ID=<your-agent-id>
```

---

## Why evaluate

Every time you change a prompt, swap a model, or add a tool, you risk regressing a case that used to work. An evaluation dataset freezes a set of representative cases so a single API call can tell you whether a change improved or hurt your agent. See [Agent Evaluation](../concepts/evaluation.md) for the concepts.

---

## Step 1 — Create a dataset

A dataset is a named container for test cases, scoped to your workspace.

```bash
curl -X POST http://localhost:8000/api/evaluation/datasets \
  -H "Authorization: Bearer $HECATE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Support Agent Regression Set",
    "description": "Core questions the support agent must keep answering correctly.",
    "metadata": {"agent_id": "'"$AGENT_ID"'"}
  }'
```

Copy the returned `id` — that is your `DATASET_ID`.

---

## Step 2 — Add test items

Items are the individual cases. Each item carries the input plus whatever reference material the evaluators need (a golden answer, expected facts, expected tool calls). Add several at once:

```bash
curl -X POST http://localhost:8000/api/evaluation/datasets/$DATASET_ID/items \
  -H "Authorization: Bearer $HECATE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '[
    {
      "input": "How do I reset my password?",
      "reference_answer": "Go to Settings → Security → Reset password, then follow the emailed link.",
      "metadata": {"category": "account"}
    },
    {
      "input": "What is your refund policy?",
      "reference_answer": "Full refund within 30 days of purchase; contact support with the order ID.",
      "metadata": {"category": "billing"}
    }
  ]'
```

The response reports how many items were added. List them any time:

```bash
curl "http://localhost:8000/api/evaluation/datasets/$DATASET_ID/items?page=1&page_size=20" \
  -H "Authorization: Bearer $HECATE_API_KEY"
```

---

## Step 3 — Run the evaluators

Creating a run **executes it immediately** — Hecate grades the dataset's items with the evaluators you choose and returns the scores in the response. Pick from the nine built-in evaluators (see [Agent Evaluation](../concepts/evaluation.md#3-evaluator)):

```bash
curl -X POST http://localhost:8000/api/evaluation/runs \
  -H "Authorization: Bearer $HECATE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "dataset_id": "'"$DATASET_ID"'",
    "evaluators": ["correctness", "relevancy", "completeness"],
    "answer_source": "reference"
  }'
```

The response includes the aggregate result up front:

```json
{
  "run_id": "...",
  "dataset_id": "...",
  "total_items": 2,
  "metric_averages": {
    "correctness": 0.85,
    "relevancy": 0.92,
    "completeness": 0.78
  },
  "total_duration_ms": 4120,
  "item_scores": { ... }
}
```

`metric_averages` is your headline signal — track it across runs. If you request an evaluator name that is not registered (for example a RAG evaluator when `ragas` is not installed), the request returns `422 INVALID_EVALUATOR` with the list of available evaluators in `details.available`.

---

## Step 4 — Inspect per-item scores

A low average usually traces to one or two specific items. Pull the per-item scores to see *why*:

```bash
curl "http://localhost:8000/api/evaluation/runs/$RUN_ID/scores?page=1&page_size=20" \
  -H "Authorization: Bearer $HECATE_API_KEY"
```

Each score includes a `reasoning` field — the evaluator's justification for the value it gave. This is where debugging starts: a `correctness: 0.4` with reasoning "the answer omits the email-link step" tells you exactly what to fix in the prompt.

The run summary also carries the averages and totals:

```bash
curl http://localhost:8000/api/evaluation/runs/$RUN_ID \
  -H "Authorization: Bearer $HECATE_API_KEY"
```

---

## Step 5 — Iterate and detect regressions

This is the point of the loop. Change your agent (edit the prompt, upgrade the model, add a tool), then re-run against the **same** dataset:

```bash
# After changing the agent... re-run the same evaluators
curl -X POST http://localhost:8000/api/evaluation/runs \
  -H "Authorization: Bearer $HECATE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "dataset_id": "'"$DATASET_ID"'",
    "evaluators": ["correctness", "relevancy", "completeness"],
    "answer_source": "reference"
  }'
```

Compare the new `metric_averages` to the previous run:

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| correctness | 0.85 | 0.88 | ✅ +0.03 |
| completeness | 0.78 | 0.71 | ❌ −0.07 — regression |

A drop is a regression signal — your change improved one thing but broke another. Without the frozen dataset, you would not have caught the completeness drop until users complained.

List all runs over a dataset to see the trend:

```bash
curl "http://localhost:8000/api/evaluation/runs?dataset_id=$DATASET_ID" \
  -H "Authorization: Bearer $HECATE_API_KEY"
```

---

## RAG evaluators (optional)

For knowledge-base agents, the four RAG evaluators (`faithfulness`, `answer_relevancy`, `context_precision`, `context_recall`) measure whether the answer is grounded in the retrieved context. They require the `ragas` package:

```bash
uv pip install ragas
```

Install it, and the four RAG evaluators become available in the registry automatically. Without `ragas`, requesting one returns `422` with the available list — so you can detect the missing dependency from the API response directly.

---

## Putting it in CI

For continuous regression checks, script the loop and fail on a threshold drop:

```bash
# Run, capture the averages, compare to a baseline threshold
RESULT=$(curl -s -X POST http://localhost:8000/api/evaluation/runs \
  -H "Authorization: Bearer $HECATE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"dataset_id":"'"$DATASET_ID"'","evaluators":["correctness"],"answer_source":"reference"}')

CORRECTNESS=$(echo "$RESULT" | python -c "import sys,json; print(json.load(sys.stdin)['metric_averages']['correctness'])")
python -c "import sys; sys.exit(0 if float('$CORRECTNESS') >= 0.80 else 1)" || echo "REGRESSION: correctness $CORRECTNESS < 0.80"
```

Run this on every agent-config change and you turn evaluation from a manual spot-check into an automated gate.

---

## Next steps

- [Agent Evaluation concepts](../concepts/evaluation.md) — the five concepts in depth
- [REST API — Evaluation](../reference/rest-api.md#evaluation) — the full endpoint list
- [Context Engineering](../concepts/context-engineering.md) — understanding why long-running agents stay coherent (a common source of evaluation surprises)
- [Knowledge Base and RAG tutorial](02-knowledge-base.md) — build the RAG agent the RAG evaluators grade
