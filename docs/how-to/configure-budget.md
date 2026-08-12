# Configure Budget & Cost Tracking

Set up per-workspace and per-agent budgets, configure degradation behavior, and monitor cost in production.

For the conceptual model, see [Budget & Cost](../concepts/budget.md). For the budget API reference, see [REST API](../reference/rest-api.md#budget).

---

## What you can configure

This guide covers:

- **Workspace budget** — monthly cost or token ceiling for a workspace
- **Per-agent budget** — stricter limit on a single agent
- **Degradation profiles** — what happens as you approach the limit
- **Cost dashboards** — viewing where spend goes
- **Responding to 429** — handling overspend in client code

---

## Step 1 — Set a workspace budget

Workspaces are the primary budget boundary. Set a monthly cost limit:

```bash
# CLI
hecate budget set --workspace ws_eng \
  --monthly-limit-usd 1000 \
  --period monthly \
  --hard-limit  # default; new requests return 429 when exceeded
```

Equivalent API:

```bash
curl -X POST http://localhost:8000/api/budgets \
  -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id": "ws_eng",
    "period": "monthly",
    "limit_usd": 1000,
    "hard_limit": true,
    "degradation_profile": "balanced"
  }'
```

Response:

```json
{
  "id": "bgt_8f3c2a1b-9d4e-4f6a-b5c7-1e2d3f4a5b6c",
  "workspace_id": "ws_eng",
  "period": "monthly",
  "limit_usd": 1000,
  "hard_limit": true,
  "degradation_profile": "balanced",
  "current_spend_usd": 0,
  "created_at": "2026-08-11T14:23:00Z"
}
```

### Period options

| Period | Resets on | Use for |
|---|---|---|
| `daily` | midnight UTC | Cost-sensitive experiments |
| `weekly` | Monday 00:00 UTC | Cost-aware teams |
| `monthly` | 1st of month 00:00 UTC | Most production deployments |
| `quarterly` | 1st of quarter | Long-running projects |

### Limit types

| Type | When exceeded | Default? |
|---|---|---|
| **Hard limit** | Return 429, no further requests | Default |
| **Soft limit** | Continue but log warning and alert | Configurable |

For most production deployments, **hard limit** is safer (prevents runaway costs).

---

## Step 2 — Configure degradation profile

As budget approaches its limit, Hecate can **degrade gracefully** instead of hard-stopping. Four built-in profiles:

| Profile | Triggers drop at | Triggers compress at | Triggers emergency at |
|---|---|---|---|
| **conservative** | 50% | 80% | 95% |
| **balanced** | 70% | 90% | 98% |
| **aggressive** | 85% | 95% | 99% |
| **none** | Never | Never | Never |

Set per workspace:

```bash
hecate budget set --workspace ws_eng \
  --degradation-profile balanced
```

Or per agent (overrides workspace):

```bash
hecate agent budget set <agent-id> \
  --monthly-limit-usd 100 \
  --degradation-profile conservative
```

### What each degradation level does

| Level | Triggers | Behavior |
|---|---|---|
| `none` | Never | Full functionality |
| `drop` | > 70% (balanced) | Drop optional tools (`web_search`, `verbose_logging`) |
| `compress` | > 90% (balanced) | Shorter conversation history, fewer examples |
| `emergency` | > 98% (balanced) | Switch to cheapest model; minimal context |

See [Budget & Cost concept](../concepts/budget.md#degradation-levels) for details.

---

## Step 3 — Set per-agent budgets (optional)

For fine-grained control, set per-agent budgets. Per-agent can be **stricter** than workspace but not more lenient.

```bash
hecate agent budget set <agent-id> \
  --monthly-limit-usd 100 \
  --period monthly \
  --fallback-behavior "downgrade-model"
```

Options for `fallback-behavior` when budget exceeded:

| Behavior | What happens |
|---|---|
| `block` | Return 429 (default) |
| `downgrade-model` | Switch to a cheaper model from the configured fallback list |
| `use-cache` | Try to answer from cached context if available |
| `queue` | Queue request for next available budget window |

### Fallback model chains

```python
# In agent config
agent.model_config = {
    "model": "gpt-4o",
    "fallback": ["gpt-4o-mini", "gpt-3.5-turbo"],
    "cost_threshold": 0.01,  # if predicted cost > 1¢, use fallback
}
```

When budget is tight, the LLM call routes to cheaper models automatically.

---

## Step 4 — View cost dashboard

### CLI

```bash
# Current spend
hecate budget status

# Per-workspace breakdown
hecate budget status --workspace ws_eng --last-30d

# Per-agent breakdown
hecate budget status --agent <agent-id> --last-30d

# Per-model breakdown
hecate budget status --group-by model --last-30d

# Export to CSV
hecate budget export --last-30d --format csv > budget.csv
```

### API

```bash
# Current status
curl http://localhost:8000/api/budgets/<budget-id>/status \
  -H "Authorization: Bearer $ADMIN_KEY"

# Historical snapshots
curl "http://localhost:8000/api/budgets/<budget-id>/snapshots?days=30" \
  -H "Authorization: Bearer $ADMIN_KEY"

# Forecast
curl http://localhost:8000/api/budgets/<budget-id>/forecast \
  -H "Authorization: Bearer $ADMIN_KEY"
```

### Canvas dashboard

The canvas's **Ops Center** tab shows:

- **Per-workspace spend** bar chart (last 30 days)
- **Per-agent spend** top-10 list
- **Per-model spend** pie chart
- **Hourly burn rate** line chart (last 24h)
- **Forecast** projected end-of-month spend

---

## Step 5 — Set up alerts

Get notified before budget is exceeded:

```bash
# CLI
hecate budget alert set \
  --workspace ws_eng \
  --threshold 80 \
  --channel slack \
  --target https://hooks.slack.com/services/...

# Multiple thresholds
hecate budget alert set --workspace ws_eng --threshold 50 ...
hecate budget alert set --workspace ws_eng --threshold 90 ...
hecate budget alert set --workspace ws_eng --threshold 100 ...
```

Alert triggers when usage crosses each threshold. State stored in `budget_alert_state` to avoid duplicate notifications.

### Alert channels

| Channel | Setup |
|---|---|
| Slack | Webhook URL from Slack incoming webhooks |
| Email | SMTP config (see [Configure LLM providers](configure-llm-providers.md)) |
| Webhook | Any HTTP endpoint (e.g., PagerDuty) |
| In-app | Default; shown in canvas |

---

## Step 6 — Respond to 429 in your client code

When a workspace exceeds budget, API calls return 429 with a `Retry-After` header:

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

### OpenAI SDK error handling

```python
from openai import OpenAI, RateLimitError
import time

client = OpenAI(base_url="http://localhost:8000/v1", api_key="...")

try:
    resp = client.chat.completions.create(...)
except RateLimitError as e:
    if "budget_exceeded" in str(e):
        # Don't retry — wait until next period
        raise
    # Normal rate limit, retry with exponential backoff
    time.sleep(60)
```

### Bulk operations: batch and retry

For batch jobs, defer work to next budget period:

```python
import datetime

def should_defer_to_next_period(retry_after_iso: str) -> bool:
    retry_after = datetime.datetime.fromisoformat(retry_after_iso)
    return (retry_after - datetime.datetime.utcnow()).days > 1
```

---

## Common patterns

### Per-team budgets in a large org

For organizations with multiple teams:

```bash
# Engineering: $5000/month
hecate budget set --workspace ws_eng --monthly-limit-usd 5000

# Marketing: $500/month
hecate budget set --workspace ws_mkt --monthly-limit-usd 500

# Finance: $200/month
hecate budget set --workspace ws_fin --monthly-limit-usd 200
```

The org-level budget (default unlimited) is the cumulative ceiling.

### Cost anomaly detection

Enable anomaly alerts:

```bash
hecate budget alert set \
  --workspace ws_eng \
  --anomaly-detection 2x \
  --channel pagerduty
```

`2x` means: if hourly spend is 2× the 7-day rolling average, alert. Helps catch runaway agents early.

### Custom pricing for negotiated rates

If you have a volume discount with OpenAI, override the default pricing:

```bash
hecate model pricing set openai gpt-4o \
  --input-price-per-1k 0.0025 \
  --output-price-per-1k 0.0075
```

This affects cost calculations across all budgets.

---

## Verifying your setup

After configuring:

```bash
# 1. Check status
hecate budget status

# 2. Make a test request
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $HECATE_API_KEYS" \
  -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "ping"}]}'

# 3. Verify cost was recorded
hecate budget status --last-5m

# 4. Check audit log
hecate audit list --event_type budget.checked --last-5m
```

---

## Troubleshooting

### "Budget exceeded" but no spend visible

If you see 429s but `hecate budget status` shows 0 spend:

```bash
# Check pending snapshots
hecate budget status --include-pending

# Force snapshot
hecate budget snapshot --workspace ws_eng

# Check audit log for actual spend
hecate audit list --event_type llm.invoked --last-1h
```

### Forecast shows zero days remaining

If `forecast_remaining()` returns 0 even though budget isn't met:

```bash
# Check for skew between recorded spend and actual LLM cost
hecate budget reconcile --workspace ws_eng

# This re-fetches usage from Model Hub and reconciles
```

### Per-agent budget not enforced

Per-agent budgets require the agent to be in a workspace with a budget configured. If not:

```bash
# Check agent's workspace
hecate agent get <agent-id> | jq .workspace_id

# Set workspace budget
hecate budget set --workspace <workspace_id> --monthly-limit-usd 1000
```

---

## Related documents

- [Budget & Cost concept](../concepts/budget.md) — conceptual model
- [Configure LLM providers](configure-llm-providers.md) — set up model pricing
- [Multi-Tenancy concept](../concepts/multi-tenancy.md) — workspace hierarchy
- [Observability concept](../concepts/observability.md) — cost metrics
- [REST API reference](../reference/rest-api.md#budget) — full budget API
- [Troubleshooting](troubleshoot.md) — common cost issues