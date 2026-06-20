## ADDED Requirements

### Requirement: ModelPricingModel ORM model
The system SHALL define `ModelPricingModel(BaseModel)` in `models/model_pricing.py` with fields: `model_id` (String 255, the provider model name e.g. "gpt-4o"), `display_name` (String 255), `input_price_per_1k` (Float, cost per 1K input tokens in USD), `output_price_per_1k` (Float, cost per 1K output tokens in USD), `currency` (String 8, default "USD"), `effective_from` (DateTime, when this pricing takes effect), `effective_until` (DateTime nullable, when this pricing expires, NULL means current), `workspace_id` (UUID, default zero UUID).

#### Scenario: Create pricing entry
- **WHEN** a `ModelPricingModel` is created with `model_id="gpt-4o"`, `input_price_per_1k=0.0025`, `output_price_per_1k=0.01`, `effective_from=2026-01-01`
- **THEN** the record is persisted with `effective_until=None` and `currency="USD"`

#### Scenario: Default currency
- **WHEN** a `ModelPricingModel` is created without specifying `currency`
- **THEN** `currency` is `"USD"`

#### Scenario: Effective range overlap prevention
- **WHEN** a new pricing entry is created for `model_id="gpt-4o"` with `effective_from` that overlaps an existing entry's effective range in the same workspace
- **THEN** the system SHALL set the previous entry's `effective_until` to the new entry's `effective_from`, ensuring only one active pricing per model at any point in time

### Requirement: ModelPricingCreateSchema
The system SHALL define `ModelPricingCreateSchema` in `models/model_pricing.py` with fields: `model_id` (str, 1-255 chars), `display_name` (str, 1-255 chars), `input_price_per_1k` (float, ≥ 0), `output_price_per_1k` (float, ≥ 0), `currency` (str, default "USD"), `effective_from` (datetime).

#### Scenario: Valid schema
- **WHEN** `ModelPricingCreateSchema(model_id="gpt-4o", display_name="GPT-4o", input_price_per_1k=0.0025, output_price_per_1k=0.01, effective_from="2026-01-01T00:00:00")` is validated
- **THEN** the schema is accepted

#### Scenario: Negative price rejected
- **WHEN** `ModelPricingCreateSchema` is constructed with `input_price_per_1k=-0.01`
- **THEN** validation fails

### Requirement: ModelPricingReadSchema
The system SHALL define `ModelPricingReadSchema` in `models/model_pricing.py` with `model_config = ConfigDict(from_attributes=True)` and all ModelPricingModel fields including `id`, `created_at`, `updated_at`.

#### Scenario: Read schema from ORM
- **WHEN** a `ModelPricingReadSchema` is created from a `ModelPricingModel` ORM instance
- **THEN** all fields including `id`, `effective_from`, `effective_until` are populated

### Requirement: Model pricing CRUD API
The system SHALL expose REST endpoints for model pricing management under `/api/model-pricing`.

#### Scenario: Create pricing
- **WHEN** `POST /api/model-pricing` is called with a valid `ModelPricingCreateSchema`
- **THEN** a new pricing record is created and returned with status 201

#### Scenario: List pricing entries
- **WHEN** `GET /api/model-pricing` is called
- **THEN** a paginated list of pricing entries is returned, ordered by `model_id`

#### Scenario: List pricing filtered by model
- **WHEN** `GET /api/model-pricing?model_id=gpt-4o` is called
- **THEN** only pricing entries for `model_id="gpt-4o"` are returned

#### Scenario: Update pricing
- **WHEN** `PUT /api/model-pricing/{id}` is called with updated `input_price_per_1k`
- **THEN** the pricing record is updated and returned

#### Scenario: Delete pricing
- **WHEN** `DELETE /api/model-pricing/{id}` is called
- **THEN** the pricing record is soft-deleted and status 204 is returned

### Requirement: Cost calculation from token usage
The system SHALL calculate cost by multiplying token counts from `TraceModel.usage` by the matching `ModelPricingModel` rates. For each trace, the cost is `(prompt_tokens / 1000 × input_price_per_1k) + (completion_tokens / 1000 × output_price_per_1k)`, using the pricing entry whose effective range contains the trace's `start_time`.

#### Scenario: Calculate cost for a single trace
- **WHEN** a trace has `usage = {"prompt_tokens": 1000, "completion_tokens": 500}` and model "gpt-4o" with `input_price_per_1k=0.0025`, `output_price_per_1k=0.01`
- **THEN** the cost is `(1000/1000 × 0.0025) + (500/1000 × 0.01) = 0.0025 + 0.005 = 0.0075` USD

#### Scenario: Trace with no matching pricing
- **WHEN** a trace references model "unknown-model" that has no pricing entry
- **THEN** the cost is `0.0` and the tokens are counted as `unpriced_tokens`

#### Scenario: Historical pricing applied correctly
- **WHEN** a trace from 2026-01-15 uses model "gpt-4o" and pricing changed on 2026-02-01
- **THEN** the cost calculation uses the pricing that was effective on 2026-01-15, not the current pricing

### Requirement: Cost summary API
The system SHALL expose `GET /api/costs/summary` that returns total cost, total tokens (input + output), and unpriced tokens for a given time range, with optional filters by `user_id`, `agent_id`, `session_id`, and `model`.

#### Scenario: Summary for a time range
- **WHEN** `GET /api/costs/summary?start_date=2026-06-01&end_date=2026-06-30` is called
- **THEN** the response contains `total_cost`, `total_input_tokens`, `total_output_tokens`, `unpriced_tokens` for June 2026

#### Scenario: Summary filtered by agent
- **WHEN** `GET /api/costs/summary?agent_id={uuid}` is called
- **THEN** only costs for traces with the specified `agent_id` are included

#### Scenario: Summary with no traces
- **WHEN** `GET /api/costs/summary` is called for a time range with no traces
- **THEN** the response contains `total_cost=0.0`, `total_input_tokens=0`, `total_output_tokens=0`, `unpriced_tokens=0`

### Requirement: Cost breakdown API
The system SHALL expose `GET /api/costs/breakdown` that returns cost aggregated by a specified dimension (`group_by` parameter: `model`, `agent`, `user`, `session`), with optional time range and filters.

#### Scenario: Breakdown by model
- **WHEN** `GET /api/costs/breakdown?group_by=model&start_date=2026-06-01&end_date=2026-06-30` is called
- **THEN** the response contains a list of `{key, cost, input_tokens, output_tokens, percentage}` entries, one per model, sorted by cost descending

#### Scenario: Breakdown by agent
- **WHEN** `GET /api/costs/breakdown?group_by=agent` is called
- **THEN** the response contains cost aggregated per `agent_id`, with `key` being the agent UUID string

#### Scenario: Breakdown with percentage calculation
- **WHEN** the total cost is $10.00 and model "gpt-4o" accounts for $6.00
- **THEN** the "gpt-4o" entry has `percentage = 60.0`

### Requirement: Cost timeseries API
The system SHALL expose `GET /api/costs/timeseries` that returns cost data points over time, with `granularity` parameter (`hourly`, `daily`, `monthly`) and optional filters.

#### Scenario: Daily timeseries
- **WHEN** `GET /api/costs/timeseries?granularity=daily&start_date=2026-06-01&end_date=2026-06-07` is called
- **THEN** the response contains 7 data points, one per day, each with `{timestamp, cost, input_tokens, output_tokens}`

#### Scenario: Timeseries filtered by model
- **WHEN** `GET /api/costs/timeseries?granularity=daily&model=gpt-4o` is called
- **THEN** only costs for "gpt-4o" traces are included in each data point

#### Scenario: Empty timeseries
- **WHEN** `GET /api/costs/timeseries` is called for a time range with no traces
- **THEN** the response contains zero-cost data points for each interval in the range

### Requirement: Seed pricing data migration
The system SHALL include an Alembic data migration that pre-populates `ModelPricingModel` with current pricing for common models: gpt-4o, gpt-4o-mini, gpt-4-turbo, claude-3.5-sonnet, claude-3.5-haiku, deepseek-chat, deepseek-reasoner, gemini-2.0-flash.

#### Scenario: Migration populates pricing
- **WHEN** the migration is applied
- **THEN** at least 8 pricing entries exist in the `model_pricings` table with `effective_from` set to the migration run date

#### Scenario: Migration is idempotent
- **WHEN** the migration is applied to a database that already has pricing entries
- **THEN** no duplicate entries are created
