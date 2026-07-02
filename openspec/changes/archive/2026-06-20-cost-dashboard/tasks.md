## 1. Models Layer — ModelPricingModel

- [x] 1.1 Define `ModelPricingModel(BaseModel)` in `models/model_pricing.py` with fields: `model_id` (String 255), `display_name` (String 255), `input_price_per_1k` (Float), `output_price_per_1k` (Float), `currency` (String 8, default "USD"), `effective_from` (DateTime), `effective_until` (DateTime nullable), `workspace_id` (UUID, default zero UUID)
- [x] 1.2 Add indexes: `idx_model_pricings_model` on (model_id, workspace_id, deleted), `idx_model_pricings_effective` on (effective_from, effective_until)
- [x] 1.3 Define `ModelPricingCreateSchema` with validation (model_id 1-255 chars, prices ≥ 0, required effective_from)
- [x] 1.4 Define `ModelPricingUpdateSchema` with all fields optional
- [x] 1.5 Define `ModelPricingReadSchema` with `ConfigDict(from_attributes=True)` and all model fields including id, created_at, updated_at
- [x] 1.6 Write model tests: create with all fields, create with defaults (currency, workspace_id), ReadSchema from attributes

## 2. Alembic Migration — Table + Seed Data

- [x] 2.1 Create Alembic migration to add `model_pricings` table with all columns and indexes (chain from current head `d4d66ddd6959`)
- [x] 2.2 Add seed data in migration: insert pricing entries for gpt-4o ($2.50/$10.00 per 1M = $0.0025/$0.01 per 1K), gpt-4o-mini ($0.15/$0.60), gpt-4-turbo ($10.00/$30.00), claude-3.5-sonnet ($3.00/$15.00), claude-3.5-haiku ($0.25/$1.25), deepseek-chat ($0.14/$0.28), deepseek-reasoner ($0.55/$2.19), gemini-2.0-flash ($0.10/$0.40)
- [x] 2.3 Set `effective_from` to migration run date (use `datetime.now(UTC)`) and `effective_until` to NULL for all seed entries
- [x] 2.4 Make seed insertion idempotent — check if entries already exist before inserting

## 3. Service Layer — Pricing CRUD + Effective Range Overlap

- [x] 3.1 Create `services/cost_service.py` with `CostService(db: AsyncSession)` class
- [x] 3.2 Implement `create_pricing(data: ModelPricingCreateSchema, workspace_id) -> ModelPricingReadSchema` — on create, set previous active entry's `effective_until` to new entry's `effective_from` (overlap prevention)
- [x] 3.3 Implement `list_pricing(workspace_id, model_id: str | None, page, page_size) -> dict` — paginated list with optional model_id filter
- [x] 3.4 Implement `update_pricing(pricing_id, data: ModelPricingUpdateSchema) -> ModelPricingReadSchema`
- [x] 3.5 Implement `delete_pricing(pricing_id) -> None` — soft delete
- [x] 3.6 Implement `get_effective_pricing(model_id: str, at_time: datetime, workspace_id) -> ModelPricingModel | None` — find pricing entry whose effective range contains `at_time`

## 4. Service Layer — Cost Calculation + Aggregation

- [x] 4.1 Implement `get_cost_summary(start_date, end_date, user_id, agent_id, session_id, model, workspace_id) -> CostSummarySchema` — aggregate total cost, total tokens, unpriced tokens from TraceModel JOIN ModelPricingModel
- [x] 4.2 Implement `get_cost_breakdown(group_by, start_date, end_date, filters, workspace_id) -> list[CostBreakdownEntrySchema]` — aggregate by model/agent/user/session with percentage calculation
- [x] 4.3 Implement `get_cost_timeseries(granularity, start_date, end_date, filters, workspace_id) -> list[CostTimeseriesPointSchema]` — time-bucketed aggregation (hourly/daily/monthly)
- [x] 4.4 Define response schemas: `CostSummarySchema`, `CostBreakdownEntrySchema`, `CostTimeseriesPointSchema` in `models/model_pricing.py`
- [x] 4.5 Handle unpriced traces — traces with model not in pricing table return cost=0 but tokens counted in `unpriced_tokens`

## 5. API Layer — Pricing CRUD Endpoints

- [x] 5.1 Create `api/management/model_pricing.py` router
- [x] 5.2 Implement `POST /api/model-pricing` — create pricing entry (201)
- [x] 5.3 Implement `GET /api/model-pricing` — list with optional `model_id` filter and pagination
- [x] 5.4 Implement `PUT /api/model-pricing/{id}` — update pricing entry
- [x] 5.5 Implement `DELETE /api/model-pricing/{id}` — soft delete (204)
- [x] 5.6 Register router in `api/management/__init__.py` or main app router setup

## 6. API Layer — Cost Query Endpoints

- [x] 6.1 Create `api/management/costs.py` router
- [x] 6.2 Implement `GET /api/costs/summary` — accepts start_date, end_date, user_id, agent_id, session_id, model query params
- [x] 6.3 Implement `GET /api/costs/breakdown` — accepts group_by (model/agent/user/session), start_date, end_date, and optional filters
- [x] 6.4 Implement `GET /api/costs/timeseries` — accepts granularity (hourly/daily/monthly), start_date, end_date, and optional filters
- [x] 6.5 Register router in `api/management/__init__.py` or main app router setup

## 7. Conftest + Tests

- [x] 7.1 Add `ModelPricingModel` import to `tests/conftest.py` if needed for model registration
- [x] 7.2 Write model tests: create pricing, default currency, ReadSchema from attributes, overlap prevention
- [x] 7.3 Write CostService tests: summary with data, summary empty, breakdown by model, breakdown percentage, timeseries daily, unpriced tokens
- [x] 7.4 Write API tests: pricing CRUD (create/list/update/delete), cost summary, cost breakdown, cost timeseries

## 8. Verification

- [x] 8.1 Run `ruff check src/hecate/ tests/` — zero errors
- [x] 8.2 Run `ruff format --check src/ tests/` — zero issues
- [x] 8.3 Run `mypy src/` — zero errors
- [x] 8.4 Run `python -m pytest tests/ -q` — all tests passing
