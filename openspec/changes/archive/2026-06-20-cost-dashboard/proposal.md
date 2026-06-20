## Why

The platform records token usage per LLM call (TraceModel.usage), but there is no way to answer "how much did this cost?" — no model pricing table, no cost calculation, no aggregation APIs. Administrators cannot track spend by user, agent, session, or model, making budget management impossible.

## What Changes

- **New `ModelPricingModel`** — DB-backed model pricing table with time-ranged pricing (effective_from / effective_until), supporting input/output per-1K-token rates and custom model definitions
- **New `CostService`** — calculates cost from TraceModel token usage × ModelPricingModel rates, with multi-dimensional aggregation (by user, agent, session, model, time range)
- **New cost API endpoints** — summary (total cost), breakdown (group-by aggregation), timeseries (daily/hourly trend)
- **Seed pricing data** — pre-populate pricing for common models (gpt-4o, gpt-4o-mini, claude-3.5-sonnet, etc.) via migration

## Capabilities

### New Capabilities
- `cost-dashboard`: Model pricing management, cost calculation from token usage, and multi-dimensional cost aggregation APIs

### Modified Capabilities
_(none — this is a purely additive feature building on existing TraceModel data)_

## Impact

- **New files**: `models/model_pricing.py` (ORM + schemas), `services/cost_service.py`, `api/management/costs.py`, Alembic migration
- **Existing data**: TraceModel.usage JSON already contains `{prompt_tokens, completion_tokens, total_tokens}` — no schema change needed
- **Dependencies**: No new external packages; uses existing SQLAlchemy async, Pydantic, FastAPI patterns
- **API surface**: New `/api/costs/*` endpoints (summary, breakdown, timeseries) + `/api/model-pricing/*` CRUD
