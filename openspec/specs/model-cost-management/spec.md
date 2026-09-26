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

### Requirement: LLM 调用参数透传与用量核算

Runtime 的 LLM 调用适配层 SHALL 把调用配置中的模型限制参数（至少 `temperature`、`max_tokens`，以及 provider 支持的 `timeout`、`num_retries`）透传给底层 LLM 服务，使其真实到达 provider。用量核算 SHALL 优先采用 provider 返回的 usage（区分输入、输出；缓存用量在 provider 提供时计入），provider usage 缺失时 SHALL 以"输入消息与累计输出全文的确定性估算"一次性结算并在日志与事件中标记估算（`estimated=true`）。核算结果 SHALL 与流式分块方式无关（分块不变性）。纯工具调用的往返（无输出文本）SHALL 仍产生输入侧用量。

#### Scenario: 模型限制参数到达 provider

- **WHEN** agent 配置 `temperature=0.2`、`max_tokens=512` 后经 runtime 执行一次 LLM 调用
- **THEN** 底层 LLM 服务收到的调用参数包含这两个值（以 mock 断言调用参数为准）

#### Scenario: usage 可用时按分项核算

- **WHEN** provider 返回 `prompt_tokens` / `completion_tokens` usage
- **THEN** 记录的用量来自 usage 数值，而非文本长度估算

#### Scenario: 改变分块方式不改变总费用

- **WHEN** 同一请求分别以大片段与小片段流式返回（内容总量相同、无 provider usage）
- **THEN** 两种情况下记录的估算用量一致

#### Scenario: 纯工具调用产生用量

- **WHEN** 一次 LLM 调用仅返回 tool_calls 而无文本输出
- **THEN** 该调用仍记录输入侧用量（非零）

#### Scenario: 缺失 usage 标记估算

- **WHEN** provider 未返回 usage，适配层完成估算结算
- **THEN** 结算记录（日志/事件）携带 `estimated=true` 标记
