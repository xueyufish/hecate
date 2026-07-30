## ADDED Requirements — 新增需求

### Requirement: 使用 ORG 和 AGENT 值扩展 QuotaScope — Extend QuotaScope with ORG and AGENT values
系统应向现有的 `QuotaScope` 枚举添加 `ORG = "org"` 和 `AGENT = "agent"`，以支持按组织和按 Agent 的预算限制。

#### Scenario: 创建组织级预算 — Create org-level budget
- **WHEN** 创建 QuotaModel 时使用 `scope="org"`、`scope_id=<org_uuid>`、`resource_type="cost"`、`limit_value=10000.0`、`window_type="monthly"`
- **THEN** 该预算应对该组织内的所有支出执行

#### Scenario: 创建 Agent 级预算 — Create agent-level budget
- **WHEN** 创建 QuotaModel 时使用 `scope="agent"`、`scope_id=<agent_uuid>`、`resource_type="cost"`、`limit_value=500.0`
- **THEN** 该预算应对该特定 Agent 的所有支出执行

### Requirement: 用于成本治理的 BudgetService — BudgetService for cost governance
系统应在 `budget/budget_service.py` 中定义 `BudgetService`，通过委托给现有的 QuotaService 和 CostService 来提供预算 CRUD、利用率跟踪、预测和计费报告。

#### Scenario: 获取预算利用率 — Get budget utilization
- **WHEN** 调用 `get_utilization(org_id)`
- **THEN** 服务应通过聚合 QuotaUsageModel 记录返回当前期间的支出金额、剩余金额、利用率百分比和软限制状态

#### Scenario: LLM 调用时的预算执行 — Budget enforcement on LLM call
- **WHEN** LLM 调用完成并记录成本
- **THEN** 系统应为所有适用的预算作用域（org、workspace、agent）调用 `QuotaService.record_usage(resource_type="cost", scope="workspace", ...)`
- **AND** 如果任何预算超过其硬限制，后续请求应以 HTTP 429 被拒绝

#### Scenario: 软限制告警 — Soft limit alert
- **WHEN** 支出超过预算的软限制阈值
- **THEN** 系统应通过 AlertService 触发告警（重用 QuotaService 中现有的 `_trigger_soft_limit_alert` 模式）

### Requirement: 预算预测投影 — Budget forecast projection
系统应在 `models/budget.py` 中定义 `BudgetForecastModel`，存储用于线性趋势预测的每日成本快照。

#### Scenario: 每日预测快照 — Daily forecast snapshot
- **WHEN** 后台作业每天运行（计划任务）
- **THEN** 系统应创建 BudgetForecastModel 记录，包含 `scope`、`scope_id`、`date`、`daily_cost`（来自当天的 CostService.get_cost_summary）

#### Scenario: 预测剩余支出 — Forecast remaining spend
- **WHEN** 月中调用 `forecast_remaining(org_id)`
- **THEN** 服务应计算 `avg_daily_cost = sum(daily_cost for last 7 days) / 7` 并返回 `projected_total = current_spend + avg_daily_cost * remaining_days`

#### Scenario: 预测超出预算警告 — Forecast exceeds budget warning
- **WHEN** 预测总额超过硬限制
- **THEN** 服务应在预测响应中返回 `will_exceed=True`，用于 UI 警告显示

### Requirement: 计费报告 API — Chargeback report API
系统应暴露 `/api/budgets/chargeback` 端点，返回按 Agent、工作区或模型在给定时间范围和范围内的成本细分。

#### Scenario: 按 Agent 计费 — Chargeback by agent
- **WHEN** GET `/api/budgets/chargeback?scope=org&scope_id={org_id}&group_by=agent&start_date=2026-07-01&end_date=2026-07-31`
- **THEN** 系统应返回每个 Agent 的成本细分，包括 `agent_id`、`agent_name`、`total_cost`、`total_tokens`、`percentage_of_total`

#### Scenario: 按工作区计费 — Chargeback by workspace
- **WHEN** GET `/api/budgets/chargeback?scope=org&group_by=workspace`
- **THEN** 系统应返回每个工作区的成本细分

#### Scenario: 按模型计费 — Chargeback by model
- **WHEN** GET `/api/budgets/chargeback?group_by=model`
- **THEN** 系统应返回每个 LLM 模型的成本细分

### Requirement: 预算管理 API — Budget management API
系统应在 `/api/budgets` 暴露 REST 端点，用于预算 CRUD 操作。

#### Scenario: 创建预算 — Create budget
- **WHEN** POST `/api/budgets` 带 `{scope, scope_id, resource_type, limit_value, soft_limit, window_type}`
- **THEN** 系统应使用指定参数创建 QuotaModel 并返回预算定义

#### Scenario: 列出预算 — List budgets
- **WHEN** GET `/api/budgets?scope=org&scope_id={org_id}`
- **THEN** 系统应返回指定范围内的所有预算定义

#### Scenario: 更新预算 — Update budget
- **WHEN** PUT `/api/budgets/{budget_id}` 带更新的 `limit_value` 或 `soft_limit`
- **THEN** 系统应更新 QuotaModel 并使配额缓存失效

#### Scenario: 获取预算状态及预测 — Get budget status with forecast
- **WHEN** GET `/api/budgets/{budget_id}/status`
- **THEN** 系统应返回 `{spent, remaining, utilization_pct, soft_limit, forecast: {projected_total, will_exceed, avg_daily_cost}}`

#### Scenario: 删除预算 — Delete budget
- **WHEN** DELETE `/api/budgets/{budget_id}`
- **THEN** 系统应软删除 QuotaModel 并使缓存失效
