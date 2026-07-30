## ADDED Requirements — 新增需求

### Requirement: 成本仪表板支持每个模型的成本趋势时间序列 — Cost dashboard supports per-model cost trend time-series
成本仪表板应扩展其聚合 API，返回适合前端趋势图表渲染的每个模型成本时间序列数据。

#### Scenario: 获取每个模型的成本趋势 — Get per-model cost trend
- **WHEN** 客户端请求 `GET /api/cost-dashboard/trends?group_by=model&granularity=daily&days=30`
- **THEN** 系统返回一个时间序列数组，每天一项，包含 `{date, model, cost, tokens}` 元组

#### Scenario: 按模型获取成本分解 — Get cost breakdown by model
- **WHEN** 客户端请求 `GET /api/cost-dashboard/breakdown?group_by=model&period=2026-07`
- **THEN** 系统返回每个模型的成本总计，按降序排列，包含占总花费的百分比
