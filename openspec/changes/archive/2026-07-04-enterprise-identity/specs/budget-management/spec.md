## ADDED Requirements

### Requirement: Extend QuotaScope with ORG and AGENT values
The system SHALL add `ORG = "org"` and `AGENT = "agent"` to the existing `QuotaScope` enum to support per-organization and per-agent budget limits.

#### Scenario: Create org-level budget
- **WHEN** a QuotaModel is created with `scope="org"`, `scope_id=<org_uuid>`, `resource_type="cost"`, `limit_value=10000.0`, `window_type="monthly"`
- **THEN** the budget SHALL be enforced for all spending within that organization

#### Scenario: Create agent-level budget
- **WHEN** a QuotaModel is created with `scope="agent"`, `scope_id=<agent_uuid>`, `resource_type="cost"`, `limit_value=500.0`
- **THEN** the budget SHALL be enforced for all spending by that specific agent

### Requirement: BudgetService for cost governance
The system SHALL define `BudgetService` in `budget/budget_service.py` that provides budget CRUD, utilization tracking, forecasting, and chargeback reports by delegating to existing QuotaService and CostService.

#### Scenario: Get budget utilization
- **WHEN** `get_utilization(org_id)` is called
- **THEN** the service SHALL return the current period's spent amount, remaining, utilization percentage, and soft limit status by aggregating QuotaUsageModel records

#### Scenario: Budget enforcement on LLM call
- **WHEN** an LLM invocation completes and cost is recorded
- **THEN** the system SHALL call `QuotaService.record_usage(resource_type="cost", scope="workspace", ...)` for all applicable budget scopes (org, workspace, agent)
- **AND** if any budget exceeds its hard limit, subsequent requests SHALL be rejected with HTTP 429

#### Scenario: Soft limit alert
- **WHEN** spending crosses a budget's soft limit threshold
- **THEN** the system SHALL trigger an alert via AlertService (reusing the existing `_trigger_soft_limit_alert` pattern from QuotaService)

### Requirement: Budget forecast projection
The system SHALL define `BudgetForecastModel` in `models/budget.py` that stores daily cost snapshots for linear trend forecasting.

#### Scenario: Daily forecast snapshot
- **WHEN** a background job runs daily (scheduled task)
- **THEN** the system SHALL create a BudgetForecastModel record with `scope`, `scope_id`, `date`, `daily_cost` (from CostService.get_cost_summary for that day)

#### Scenario: Forecast remaining spend
- **WHEN** `forecast_remaining(org_id)` is called mid-month
- **THEN** the service SHALL compute `avg_daily_cost = sum(daily_cost for last 7 days) / 7` and return `projected_total = current_spend + avg_daily_cost * remaining_days`

#### Scenario: Forecast exceeds budget warning
- **WHEN** the projected total exceeds the hard limit
- **THEN** the service SHALL return `will_exceed=True` in the forecast response for UI warning display

### Requirement: Chargeback report API
The system SHALL expose `/api/budgets/chargeback` endpoint that returns cost breakdown by agent, workspace, or model for a given time range and scope.

#### Scenario: Chargeback by agent
- **WHEN** GET `/api/budgets/chargeback?scope=org&scope_id={org_id}&group_by=agent&start_date=2026-07-01&end_date=2026-07-31`
- **THEN** the system SHALL return cost breakdown per agent including `agent_id`, `agent_name`, `total_cost`, `total_tokens`, `percentage_of_total`

#### Scenario: Chargeback by workspace
- **WHEN** GET `/api/budgets/chargeback?scope=org&group_by=workspace`
- **THEN** the system SHALL return cost breakdown per workspace

#### Scenario: Chargeback by model
- **WHEN** GET `/api/budgets/chargeback?group_by=model`
- **THEN** the system SHALL return cost breakdown per LLM model

### Requirement: Budget management API
The system SHALL expose REST endpoints at `/api/budgets` for budget CRUD operations.

#### Scenario: Create budget
- **WHEN** POST `/api/budgets` with `{scope, scope_id, resource_type, limit_value, soft_limit, window_type}`
- **THEN** the system SHALL create a QuotaModel with the specified parameters and return the budget definition

#### Scenario: List budgets
- **WHEN** GET `/api/budgets?scope=org&scope_id={org_id}`
- **THEN** the system SHALL return all budget definitions for the specified scope

#### Scenario: Update budget
- **WHEN** PUT `/api/budgets/{budget_id}` with updated `limit_value` or `soft_limit`
- **THEN** the system SHALL update the QuotaModel and invalidate the quota cache

#### Scenario: Get budget status with forecast
- **WHEN** GET `/api/budgets/{budget_id}/status`
- **THEN** the system SHALL return `{spent, remaining, utilization_pct, soft_limit, forecast: {projected_total, will_exceed, avg_daily_cost}}`

#### Scenario: Delete budget
- **WHEN** DELETE `/api/budgets/{budget_id}`
- **THEN** the system SHALL soft-delete the QuotaModel and invalidate cache
