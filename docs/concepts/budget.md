# Budget & Cost

LLM API calls cost money. Hecate's budget and cost system gives you **visibility** and **control** over how much each workspace, agent, and user spends — and what happens when limits are reached.

This document explains the **conceptual model**: how costs are tracked, budgets are set, and over-budget behavior is handled. For implementation details, see [Model Hub](model-hub.md) and [Multi-Tenancy](multi-tenancy.md). For the operational API, see [Cost Dashboard](../how-to/) docs.

---

## What is being tracked

Hecate tracks **four token metrics** per LLM call:

| Metric | Description | Cost weight |
|---|---|---|
| **Prompt tokens** | Input to the LLM | Varies by model |
| **Completion tokens** | Output from the LLM | Usually 3-5× prompt cost |
| **Cache read tokens** | Tokens read from prompt cache (Anthropic / OpenAI) | Often 0.1× prompt cost |
| **Cache write tokens** | Tokens written to prompt cache | Often 1.25× prompt cost |

These are reported by the LLM provider in the response. Hecate captures them per-call, aggregates per session/agent/workspace, and stores them in the budget tracking system.

### Cost calculation

Cost is computed from token counts and per-model pricing:

```
cost = (prompt_tokens × prompt_price_per_1k)
     + (completion_tokens × completion_price_per_1k)
     + (cache_read_tokens × cache_read_price_per_1k)
     + (cache_write_tokens × cache_write_price_per_1k)
```

Per-model pricing is maintained in Hecate's Model Hub (see [Model Hub](model-hub.md)). Pricing is loaded from:
- **Built-in defaults** for major providers (OpenAI, Anthropic, etc.)
- **Database overrides** for custom pricing (volume discounts, negotiated rates)
- **Periodic refresh** of provider-published pricing (planned P3)

---

## The budget hierarchy

Hecate enforces budgets at **three levels**:

```
Organization budget (e.g., $10,000 / month)
│
├── Workspace A budget (e.g., $5,000 / month)
│   ├── Agent X1 budget (e.g., $500 / month)
│   ├── Agent X2 budget (e.g., $1,000 / month)
│   └── ...
│
├── Workspace B budget (e.g., $3,000 / month)
│   └── ...
│
└── Workspace C budget (e.g., $2,000 / month)
    └── ...
```

Each level can have its own budget. A child budget cannot exceed its parent. This is enforced at request time (before the LLM call).

### Three budget dimensions

Budgets can be set on any combination of:

| Dimension | Example | When to use |
|---|---|---|
| **Total cost** | `$5,000 / month` | Hard ceiling |
| **Total tokens** | `100M tokens / month` | Provider cost predictability |
| **Per-request** | `10,000 tokens / request` | Prevent runaway single calls |

Most production deployments use **all three** as defense in depth.

---

## Budget lifecycle

```
              set budget
   ┌──────────────────────────────────────┐
   │                                      ▼
┌──────────┐  start    ┌──────────┐  fill  ┌────────────┐
│  Set    │ ────────▶│ Active   │ ─────▶│  Used     │
└──────────┘          └──────────┘        └────────────┘
                              │                │
                              │ reset /        │ expire
                              │ modify         ▼
                              ▼           ┌────────────┐
                       ┌──────────┐       │  Expired   │
                       │  Reset  │       └────────────┘
                       └──────────┘
```

| State | What it means |
|---|---|
| **Set** | Budget configured, not yet active (waiting for period start) |
| **Active** | Period running; cumulative spend tracking |
| **Used** | (transient) — spending against budget now |
| **Expired** | Period ended; budget not yet renewed |

Budgets are **periodic** (daily / weekly / monthly). When a period ends, the budget is reset to the configured amount.

### Snapshot mechanism

Hecate takes daily snapshots of budget utilization:

```python
# From src/hecate/models/budget.py
class BudgetSnapshotModel(BaseModel):
    session_id: uuid.UUID
    total_budget: int
    tokens_used: int
    tokens_remaining: int
    degradation_level: str  # "none" / "drop" / "compress" / "emergency"
```

This allows:
- **Historical analysis** — "how did we use our budget last month?"
- **Forecasting** — "at this rate, we'll exceed budget by Friday"
- **Audit** — what was the degradation level at each point?

---

## Degradation levels

When a budget approaches its limit, Hecate can **degrade gracefully** instead of hard-stopping. Four levels:

| Level | Trigger | Behavior |
|---|---|---|
| **none** | Budget normal | Full functionality |
| **drop** | > 80% used | Drop optional tools (e.g., `web_search`, `verbose_logging`) |
| **compress** | > 95% used | Compress context (shorter history, fewer examples) |
| **emergency** | > 100% used | Switch to cheapest model; no tool calls; minimal context |

Configure per workspace:

```bash
hecate budget set --workspace ws_eng --monthly-limit 1000 \
  --degradation-profile "conservative"
# Profile: aggressive / balanced / conservative
```

The `DegradationLevel` enum:

```python
class DegradationLevel(StrEnum):
    NONE = "none"
    DROP = "drop"      # drop optional tools
    COMPRESS = "compress"  # compress context
    EMERGENCY = "emergency"  # cheapest model + minimal
```

---

## Cost optimization levers

When budget is tight, several optimization levers are available:

| Lever | Where | Effort |
|---|---|---|
| **Use cheaper model** | Model Hub config | Trivial |
| **Switch to smaller model tier** | `model_config` field | Trivial |
| **Enable prompt caching** | Provider-specific | Low |
| **Compress conversation history** | Context Engineering | Low |
| **Reduce tool calling** | Hook config | Low |
| **Disable expensive tools** | `--tools ""` on agent | Trivial |
| **Use batch API** (50% discount) | OpenAI / Anthropic | Medium |
| **Switch to local model** | Model Hub → Ollama | Medium |

The `BudgetService` provides `forecast_remaining()` to predict when budget will be exhausted:

```python
# From src/hecate/budget/budget_service.py
class BudgetService:
    async def forecast_remaining(
        self, workspace_id, current_spend, days_elapsed
    ) -> int:
        """Predict remaining days of budget at current burn rate."""
```

---

## Cost visibility

### Dashboard

The canvas's **Ops Center** tab shows:

- **Per-workspace spend** — bar chart of last 30 days
- **Per-agent spend** — top 10 agents by cost
- **Per-model spend** — pie chart of model usage
- **Hourly burn rate** — line chart of last 24h
- **Forecast** — projected end-of-month spend

### CLI

```bash
# Current usage
hecate budget status

# Per-workspace breakdown
hecate budget status --workspace ws_eng --last-30d

# Per-agent breakdown
hecate budget status --agent <agent-id> --last-30d

# Export to CSV
hecate budget export --last-30d --format csv > budget.csv
```

### API

```bash
GET /api/budget/status
GET /api/budget/snapshots?workspace_id=X&days=30
POST /api/budget/set
```

---

## Budget enforcement

When a budget is exceeded, what happens?

| Scenario | Default behavior | Configurable |
|---|---|---|
| Workspace at 100% | New requests return 429 with `Retry-After` | `BUDGET_OVERFLOW_POLICY` |
| Agent at 100% | Same — agents inherit workspace limits | Per-agent overrides |
| Org at 100% | All workspaces in org get 429 | Per-org overrides |
| **Hard limit** | All LLM calls blocked | `BUDGET_HARD_LIMIT` |
| **Soft limit** | Continue but warn | `BUDGET_SOFT_LIMIT` |

The 429 response includes:

```json
{
  "error": "budget_exceeded",
  "workspace_id": "ws_eng",
  "limit_usd": 1000,
  "current_usd": 1050,
  "period": "2026-08",
  "retry_after": "2026-09-01T00:00:00Z"
}
```

Apps can detect this and fall back to cheaper models or queue requests.

---

## Cost attribution

Where does a LLM call's cost get attributed?

| Attribute | Used for |
|---|---|
| **Workspace** | Per-workspace quota |
| **Agent** | Per-agent quota |
| **User** (if known) | Chargeback to user |
| **Feature** (custom) | Product-level cost analysis |

Default: cost is attributed to the **workspace** that owns the agent. Custom attribution (e.g., per-feature) requires explicit configuration.

The `BudgetService.create_chargeback()` method records explicit cost allocations:

```python
await budget_service.create_chargeback(
    workspace_id=ws.id,
    amount=Decimal("0.05"),
    description="Research task for user Alice",
    metadata={"user_id": "alice", "feature": "research"},
)
```

---

## Cost vs quality trade-offs

Cheaper models have lower quality. Hecate lets you configure **per-agent** model tiers:

```yaml
# agent definition
model_config:
  model: "gpt-4o-mini"     # cheap
  fallback: "gpt-4o"       # expensive but better
  cost_threshold: 0.01     # if predicted cost > threshold, use fallback
```

Or per-skill:

```yaml
# skill definition
model_config:
  default: "gpt-4o-mini"
  escalation:
    - if: "confidence < 0.7"
      then: "gpt-4o"
```

This is the **cost-quality frontier** — agents don't always need the best model, but they can fall back when needed.

---

## Circuit breakers

In addition to budgets, Hecate has **circuit breakers** (per provider):

| Trigger | Behavior |
|---|---|
| 5 consecutive 5xx errors | Open circuit for 60s |
| 10 errors in 1 minute | Open circuit for 5min |
| Rate limit (429) | Back off exponentially |
| Cost anomaly (200% of expected) | Pause and alert |

Circuit breakers prevent runaway costs from misconfigured agents or provider outages.

---

## What NOT in budget

| Feature | Status |
|---|---|
| Per-user quotas | Org/workspace only; per-user via custom chargeback |
| Auto-scaling budget with workload | Static budgets only |
| Cost forecasting with ML | Simple linear forecast only |
| Multi-currency / exchange rate handling | All in USD |
| Chargeback to external billing (Stripe, etc.) | Not integrated |

---

## Related documents

- [Model Hub](model-hub.md) — how pricing is loaded and per-model tokens are counted
- [Multi-Tenancy](multi-tenancy.md) — workspace quotas and rate limits
- [Observability](observability.md) — metrics for cost visibility
-  — per-user quotas and forecasting improvements
- [How-to: Manage budget](../how-to/) — operational recipe (when written)