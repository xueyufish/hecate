## ADDED Requirements

### Requirement: Cost dashboard supports per-model cost trend time-series
The cost dashboard SHALL extend its aggregation API to return per-model cost time-series data suitable for frontend trend chart rendering.

#### Scenario: Get per-model cost trend
- **WHEN** a client requests `GET /api/cost-dashboard/trends?group_by=model&granularity=daily&days=30`
- **THEN** the system returns a time-series array with one entry per day containing `{date, model, cost, tokens}` tuples

#### Scenario: Get cost breakdown by model
- **WHEN** a client requests `GET /api/cost-dashboard/breakdown?group_by=model&period=2026-07`
- **THEN** the system returns per-model cost totals sorted descending, with percentage of total spend
