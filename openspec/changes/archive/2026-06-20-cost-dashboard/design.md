## Context

The platform already records token usage per LLM call in `TraceModel.usage` (JSON: `{prompt_tokens, completion_tokens, total_tokens}`). The `MetricsStore` tracks real-time `tokens.input` / `tokens.output` counters. What's missing is a pricing layer to convert tokens into monetary cost, and aggregation APIs to answer "how much did we spend, broken down by X?"

LangFuse uses a separate `prices` table with per-usage-type pricing and calculates cost at ingestion time. LiteLLM uses config-file pricing (`input_cost_per_token`). Neither approach handles price changes over time correctly for historical data.

## Goals / Non-Goals

**Goals:**
- DB-backed model pricing management with time-ranged validity (prices change, historical costs must remain accurate)
- Cost calculation from existing TraceModel token data × ModelPricingModel rates
- Multi-dimensional aggregation: by user, agent, session, model, time range
- REST API endpoints for cost summary, breakdown, and timeseries

**Non-Goals:**
- Real-time cost alerting (covered by future 8.6 Alerting feature)
- Budget enforcement / request blocking (LiteLLM proxy handles this)
- UI dashboard rendering (API-only; frontend consumes endpoints)
- Prompt caching cost adjustments (future enhancement)

## Decisions

### D1: Separate ModelPricingModel with time-ranged pricing

**Choice**: New `ModelPricingModel` table with `effective_from` / `effective_until` datetime fields.

**Rationale**: Model prices change frequently (OpenAI adjusts pricing, new models launch). Time-ranged pricing ensures historical cost calculations remain accurate even after price updates. This matches LangFuse's approach but with explicit time validity instead of "new prices only apply to new traces."

**Alternatives considered**:
- *Extend ModelRegistryModel with price columns*: Simpler, but cannot handle price changes — historical costs would be wrong after an update.
- *Config-file pricing (LiteLLM style)*: No DB management, but not queryable via API and harder to update at runtime.

### D2: Query-time cost calculation

**Choice**: Calculate cost at query time by JOIN-ing TraceModel with ModelPricingModel on model name + time range.

**Rationale**: Since pricing is time-ranged, storing pre-calculated cost in TraceModel would require backfilling when prices change. Query-time calculation is always accurate and the trace table is not excessively large for typical workloads.

**Alternatives considered**:
- *Write-time calculation (LangFuse)*: Faster queries, but requires backfill on price changes and loses historical accuracy.
- *Hybrid (write-time + periodic recalculation)*: Over-engineering for current scale.

### D3: Model name as the join key

**Choice**: Join TraceModel to ModelPricingModel using the model name extracted from trace metadata/usage.

**Rationale**: TraceModel doesn't have a dedicated `model` column — the model name is stored in trace `name` or `metadata`. The cost service will accept an explicit `model` parameter in queries, sourced from trace metadata at the service layer. ModelPricingModel uses `model_id` (string, e.g., "gpt-4o") as the natural key within a workspace.

### D4: Seed pricing via Alembic data migration

**Choice**: Pre-populate ModelPricingModel with current pricing for common models (gpt-4o, gpt-4o-mini, claude-3.5-sonnet, claude-3.5-haiku, deepseek-chat, etc.) in the migration.

**Rationale**: Out-of-the-box cost tracking without requiring manual pricing setup. Users can override or add custom models via API.

## Risks / Trade-offs

- **Query performance on large trace tables** → Mitigation: Add indexes on TraceModel(session_id, agent_id, start_time). For very large deployments, future enhancement can add a pre-aggregated cost snapshot table.
- **Model name mismatch between trace and pricing** → Mitigation: Cost service returns `unpriced_tokens` count alongside priced cost, so administrators can identify models without pricing configured.
- **Pricing data staleness** → Mitigation: Seed migration covers common models; API allows runtime updates. Future enhancement: sync from LiteLLM's model cost map.
