## ADDED Requirements — 新增需求

### Requirement: 系统提供模型性能聚合 API — System provides model performance aggregation API
系统应将 TraceModel 数据聚合成每个模型的性能指标：平均延迟、TTFT（首 token 时间）、错误率、token 吞吐量和请求数——按时间范围和模型分组。

#### Scenario: 获取模型性能趋势 — Get model performance trends
- **WHEN** 客户端请求 `GET /api/monitoring/models/{model_id}/performance?start=2026-07-01&end=2026-07-31&granularity=daily`
- **THEN** 系统返回指定模型的每日时间序列，包含 avg_latency、ttft、error_rate、request_count 和 cost

#### Scenario: 并排比较模型 — Compare models side by side
- **WHEN** 客户端请求 `GET /api/monitoring/models/compare?models=gpt-4o,claude-3.5,human-eval&metric=latency`
- **THEN** 系统返回一个比较矩阵，包含每个模型在默认周期（7 天）内指定指标的统计信息

### Requirement: 系统检测性能漂移 — System detects performance drift
系统应将 Z-score 异常检测（与成本异常检测相同的算法）应用于每日性能指标（延迟、错误率），并在当前性能显著偏离滚动基线时标记漂移。

#### Scenario: 检测到延迟漂移 — Latency drift detected
- **WHEN** 模型 X 的平均每日延迟超过 30 天滚动均值的 2.5 个标准差
- **THEN** 系统记录一个漂移事件，包含严重性、指标名称、当前值和基线值

#### Scenario: 检测到错误率漂移 — Error rate drift detected
- **WHEN** 每日错误率超过 Z-score 阈值
- **THEN** 系统记录一个严重漂移事件，并将其包含在监控仪表板的告警推送中

### Requirement: 前端模型监控仪表板显示趋势 — Frontend model monitoring dashboard displays trends
系统应提供一个 React 仪表板页面 `/settings/models/monitoring`，使用 Recharts 显示每个模型的趋势图表（延迟、成本、错误率），包含模型选择器、时间范围选择器和指标切换。

#### Scenario: 查看延迟趋势图 — View latency trend chart
- **WHEN** 用户导航到监控仪表板，选择模型 "gpt-4o"、指标 "latency" 和最近 7 天
- **THEN** 仪表板应渲染一个折线图，显示每日平均延迟，并带有交互式提示框

#### Scenario: 查看成本分解环形图 — View cost breakdown donut chart
- **WHEN** 用户选择工作区的成本视图
- **THEN** 仪表板应渲染一个环形图，显示所选周期内按模型划分的成本分布

### Requirement: 前端模型比较视图显示并排指标 — Frontend model comparison view displays side-by-side metrics
系统应提供一个比较视图，以表格形式并排显示所选模型的延迟、成本、错误率和能力徽章。

#### Scenario: 比较三个模型 — Compare three models
- **WHEN** 用户选择模型 "gpt-4o"、"claude-3.5-sonnet" 和 "llama-3-70b" 进行比较
- **THEN** 比较视图应显示一个表格，每行一个模型，列为平均延迟、每 1K token 成本、错误率、上下文窗口和能力徽章

### Requirement: 前端成本分析页面显示每个模型的支出 — Frontend cost analysis page shows per-model spend
系统应在 `/settings/models/cost-analysis` 提供一个成本分析页面，包含每个模型的支出分解、预算利用率条、异常时间线和预测投影。

#### Scenario: 查看月度成本分解 — View monthly cost breakdown
- **WHEN** 用户导航到 2026 年 7 月的成本分析页面
- **THEN** 页面显示每个模型的成本柱状图、预算利用率仪表盘、近期异常列表和月度预测

#### Scenario: 预算超限指示器 — Budget exceeded indicator
- **WHEN** 工作区支出超过月度预算的 80%
- **THEN** 成本分析页面应显示一个警告横幅，包含当前支出、预算上限和预测的月末支出
