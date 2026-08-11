# Agent Evaluation

An agent that works once is not an agent that works reliably. Prompts drift, tools change, models get upgraded, and a configuration that passed manual testing can silently regress in production. **Agent Evaluation** is Hecate's built-in system for measuring agent quality systematically — the same loop that华为 AgentArts, Google Vertex AI, and Palantir AIP all expose as a first-class capability.

Evaluation is not a single metric. It is a loop: define a **dataset** of test cases, run a set of **evaluators** over your agent's answers, read the **scores**, and iterate. Hecate automates the run and the scoring; you provide the test cases and pick the evaluators.

---

## Why built-in evaluation matters

Manual testing (trying a few prompts and judging the replies) does not scale:

- **It is not repeatable.** A change to a prompt or a model upgrade needs to be checked against every important case, every time.
- **It is not objective.** "Looks better" is not a metric you can track over time.
- **It misses regressions.** A tweak that fixes one case often silently breaks another that nobody re-tested.

Hecate's evaluation API turns "does my agent still work?" into a question you can answer with a single API call, against a frozen dataset, with stable metrics. Run it on every agent change, and you get a regression signal instead of a surprise.

---

## The core concepts

There are five things to know. The API lives under `/api/evaluation` (see the [REST API reference](../reference/rest-api.md)).

### 1. Evaluation Dataset

A named, workspace-scoped collection of test cases. A dataset holds **items** and carries a name, description, and metadata. Datasets are soft-deletable and versioned by virtue of being immutable test inputs — you edit the items, not the dataset's identity.

### 2. Evaluation Item

A single test case inside a dataset. An item represents one input the agent should handle, paired with whatever reference information the evaluators need (a golden answer, expected tool calls, expected facts). Items are what evaluators score against.

### 3. Evaluator

A named metric that grades an agent's answer. Hecate ships **nine built-in evaluators** in two families; you pick which ones to run per evaluation:

| Family | Evaluators | What they measure | Requirement |
|--------|-----------|-------------------|-------------|
| **Agent quality** | `correctness`, `relevancy`, `completeness`, `tool_call_accuracy`, `task_completion` | Is the answer right, on-topic, complete, and did the agent call the right tools and finish the task? | Always available |
| **RAG quality** | `context_precision`, `context_recall`, `faithfulness`, `answer_relevancy` | For knowledge-retrieval agents: is the retrieved context relevant, is the answer grounded in it, does it cite correctly? | Optional — requires `ragas` installed |

RAG evaluators are registered only when the `ragas` dependency is present; without it, requesting one returns an `INVALID_EVALUATOR` error listing what *is* available. You can also extend the registry with custom evaluator classes (see the [Extension Points](../reference/extension-points.md) and the evaluation service layer).

### 4. Evaluation Run

The act of grading. Creating a run (`POST /api/evaluation/runs`) **executes immediately** — it runs the selected evaluators over the dataset's items and returns aggregated results. A run is not a long-lived job you poll; the POST response already contains the scores.

A run carries an **answer source** (`answer_source`) that tells the engine where the agent's answers come from — freshly generated, a previous batch, or reference answers — so you can evaluate answers you already have without regenerating them.

### 5. Evaluation Score

The per-item, per-metric grade. Each score has a `metric_name`, a numeric `value`, a `reasoning` field (the evaluator's justification, useful for debugging *why* a low score was given), and a `source`. Runs aggregate scores into `metric_averages` so you can see "correctness averaged 0.82 across the dataset" at a glance.

---

## The evaluation loop

```
┌──────────────────────────────────────────────────┐
│  1. Create a dataset of representative cases      │
│     POST /api/evaluation/datasets                 │
│     POST /api/evaluation/datasets/{id}/items      │
│                                                   │
│  2. Run evaluators over the dataset               │
│     POST /api/evaluation/runs                     │
│       { dataset_id, evaluators, answer_source }   │
│                                                   │
│  3. Read the scores                               │
│     GET /api/evaluation/runs/{run_id}             │
│       → metric_averages, total_duration_ms        │
│     GET /api/evaluation/runs/{run_id}/scores      │
│       → per-item reasoning                        │
│                                                   │
│  4. Change the agent (prompt / model / tools)     │
│     and re-run → compare metric_averages          │
│     ↑ regression if a metric drops                │
└──────────────────────────────────────────────────┘
```

The loop is what makes evaluation useful. A single run tells you a snapshot; a sequence of runs across agent changes tells you whether you are improving or regressing.

---

## Choosing evaluators

| You are evaluating... | Use |
|----------------------|-----|
| A Q&A or task agent | `correctness`, `relevancy`, `completeness`, `task_completion` |
| An agent that calls tools | add `tool_call_accuracy` |
| A RAG / knowledge-base agent (requires `ragas`) | `faithfulness`, `answer_relevancy`, `context_precision`, `context_recall` |
| A multi-step workflow | `task_completion` + `tool_call_accuracy` across the trace |

Mix freely — a single run can apply any subset of the registered evaluators. Start with one or two, expand as your dataset matures.

---

## What evaluation does *not* do

- It does not run your agent end-to-end by itself. You provide the answers to grade (or point `answer_source` at where they should come from). It scores, it does not orchestrate the conversation.
- It does not replace live monitoring. Evaluation catches regressions *before* release against frozen cases; production traces catch the cases your dataset didn't anticipate. See [Monitor with OpenTelemetry](../how-to/monitor-opentelemetry.md) for the production side.
- It does not define "good" for you. The evaluators produce numbers; what threshold counts as acceptable is your call.

---

## Further reading

- [Evaluate an Agent tutorial](../tutorials/08-agent-evaluation.md) — hands-on: build a dataset, run evaluators, read scores, iterate
- [REST API reference — Evaluation](../reference/rest-api.md#evaluation) — the `/api/evaluation` endpoints
- [Ops Center design](../design/ops-center-design.md) — where evaluation fits in the broader observability and quality picture
