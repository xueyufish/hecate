# model-cost-management Specification

## Purpose
TBD - created by archiving change model-hub-completion. Update Purpose after archive.
## Requirements
### Requirement: System supports hierarchical cost budgets
The system SHALL support cost budgets at three levels: workspace (global cap), agent (per-agent cap), and user (per-user cap). Each budget specifies a limit amount, period (daily/weekly/monthly), and currency.

#### Scenario: Create workspace-level budget
- **WHEN** an administrator creates a budget with `scope: "workspace"`, `limit: 100.0`, `period: "monthly"`, `currency: "USD"`
- **THEN** the system stores the budget and enforces it for all model invocations within that workspace

#### Scenario: Agent-level budget overrides workspace budget
- **WHEN** an agent has a `$50/month` budget and the workspace has a `$100/month` budget
- **THEN** the agent SHALL be capped at `$50` regardless of the workspace limit

#### Scenario: Budget period reset
- **WHEN** a monthly budget period ends
- **THEN** the spent counter SHALL reset to zero and the next period begins automatically

### Requirement: System detects cost anomalies using z-score
The system SHALL compute daily spend per model and per workspace, then apply z-score anomaly detection (rolling 30-day window, configurable threshold default 2.5 standard deviations) to flag unusual spending patterns.

#### Scenario: Normal spend not flagged
- **WHEN** daily spend is within 2.5 standard deviations of the 30-day rolling mean
- **THEN** no anomaly is recorded

#### Scenario: Spending spike detected
- **WHEN** daily spend exceeds 2.5 standard deviations above the 30-day rolling mean
- **THEN** the system records an anomaly with severity (`info` / `warn` / `critical` based on z-score magnitude), the affected model, and the actual vs expected spend

#### Scenario: Cold start period
- **WHEN** fewer than 7 days of historical data exists
- **THEN** anomaly detection SHALL be skipped until sufficient baseline data accumulates

### Requirement: System enforces configurable budget policy
The system SHALL support two enforcement policies per budget: `"alert"` (log + notify, requests proceed) and `"block"` (PreLLMHook intercepts, request rejected with `BudgetExceededError`).

#### Scenario: Alert policy on budget exceeded
- **WHEN** spend reaches the budget limit and policy is `"alert"`
- **THEN** the system SHALL emit an alert event and continue processing requests normally

#### Scenario: Block policy on budget exceeded
- **WHEN** spend reaches the budget limit and policy is `"block"`
- **THEN** subsequent LLM invocations SHALL be intercepted by PreLLMHook and rejected with `BudgetExceededError` containing the budget details and remaining amount (zero)

#### Scenario: Block policy allows non-LLM operations
- **WHEN** budget is exceeded with `"block"` policy
- **THEN** non-LLM operations (tool calls, knowledge queries) SHALL proceed normally — only LLM invocations are blocked

### Requirement: System forecasts monthly spend
The system SHALL project end-of-period spend using linear regression on daily spend data, returning projected amount, confidence interval, and projected overrun (projected minus budget).

#### Scenario: Forecast under budget
- **WHEN** projected monthly spend is `$80` against a `$100` budget
- **THEN** the forecast SHALL return `{projected: 80.0, status: "healthy", overrun: 0.0}`

#### Scenario: Forecast over budget
- **WHEN** projected monthly spend is `$120` against a `$100` budget
- **THEN** the forecast SHALL return `{projected: 120.0, status: "warning", overrun: 20.0}`

### Requirement: System generates chargeback reports
The system SHALL aggregate costs by team/project/customer dimension and generate chargeback reports with per-dimension totals, top model contributors, and period-over-period comparison.

#### Scenario: Generate monthly chargeback
- **WHEN** an administrator requests a chargeback report for period `2026-07`
- **THEN** the system returns per-agent cost breakdown with model-level detail, total workspace spend, and comparison to previous month

