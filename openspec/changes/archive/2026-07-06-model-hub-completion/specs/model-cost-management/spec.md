## ADDED Requirements — 新增需求

### Requirement: 系统支持分层成本预算 — System supports hierarchical cost budgets
系统应在三个层级支持成本预算：工作区（全局上限）、Agent（每个 Agent 上限）和用户（每个用户上限）。每个预算指定额度、周期（daily/weekly/monthly）和货币。

#### Scenario: 创建工作区级预算 — Create workspace-level budget
- **WHEN** 管理员创建预算，`scope: "workspace"`、`limit: 100.0`、`period: "monthly"`、`currency: "USD"`
- **THEN** 系统存储该预算，并对该工作区内的所有模型调用强制执行

#### Scenario: Agent 级预算覆盖工作区预算 — Agent-level budget overrides workspace budget
- **WHEN** Agent 的预算为 `$50/month`，工作区的预算为 `$100/month`
- **THEN** Agent 的支出上限应为 `$50`，不受工作区限制的影响

#### Scenario: 预算周期重置 — Budget period reset
- **WHEN** 月度预算周期结束
- **THEN** 已花费计数器应重置为零，下一个周期自动开始

### Requirement: 系统使用 Z-score 检测成本异常 — System detects cost anomalies using z-score
系统应按模型和工作区计算每日支出，然后应用 Z-score 异常检测（滚动 30 天窗口，可配置阈值，默认 2.5 个标准差）来标记异常的消费模式。

#### Scenario: 正常消费不被标记 — Normal spend not flagged
- **WHEN** 每日支出在 30 天滚动均值的 2.5 个标准差范围内
- **THEN** 不记录异常

#### Scenario: 消费高峰被检测到 — Spending spike detected
- **WHEN** 每日支出超过 30 天滚动均值的 2.5 个标准差以上
- **THEN** 系统记录异常，包含严重性（基于 Z-score 幅度的 `info`/`warn`/`critical`）、受影响的模型以及实际支出与预期支出的对比

#### Scenario: 冷启动期 — Cold start period
- **WHEN** 不足 7 天的历史数据可用
- **THEN** 在积累足够基线数据之前，应跳过异常检测

### Requirement: 系统强制执行可配置的预算策略 — System enforces configurable budget policy
系统应为每个预算支持两种执行策略：`"alert"`（记录 + 通知，请求继续）和 `"block"`（PreLLMHook 拦截，请求被拒绝并返回 `BudgetExceededError`）。

#### Scenario: 超预算时的告警策略 — Alert policy on budget exceeded
- **WHEN** 支出达到预算上限且策略为 `"alert"`
- **THEN** 系统应发出告警事件，并正常继续处理请求

#### Scenario: 超预算时的阻断策略 — Block policy on budget exceeded
- **WHEN** 支出达到预算上限且策略为 `"block"`
- **THEN** 后续的 LLM 调用被 PreLLMHook 拦截，并拒绝返回 `BudgetExceededError`，包含预算详情和剩余额度（零）

#### Scenario: 阻断策略允许非 LLM 操作 — Block policy allows non-LLM operations
- **WHEN** 预算超限且策略为 `"block"`
- **THEN** 非 LLM 操作（工具调用、知识查询）应正常进行——只有 LLM 调用被阻断

### Requirement: 系统预测月度支出 — System forecasts monthly spend
系统应使用每日支出数据的线性回归预测期末支出，返回预测金额、置信区间和预测超支（预测值减去预算）。

#### Scenario: 预测在预算内 — Forecast under budget
- **WHEN** 预测月度支出为 `$80`，预算是 `$100`
- **THEN** 预测应返回 `{projected: 80.0, status: "healthy", overrun: 0.0}`

#### Scenario: 预测超预算 — Forecast over budget
- **WHEN** 预测月度支出为 `$120`，预算是 `$100`
- **THEN** 预测应返回 `{projected: 120.0, status: "warning", overrun: 20.0}`

### Requirement: 系统生成费用分摊报告 — System generates chargeback reports
系统应按团队/项目/客户维度聚合成本，生成费用分摊报告，包含每个维度的总计、主要模型贡献者和环比比较。

#### Scenario: 生成月度费用分摊报告 — Generate monthly chargeback
- **WHEN** 管理员请求 2026 年 7 月的费用分摊报告
- **THEN** 系统返回每个 Agent 的成本分解，包含模型级详情、工作区总支出以及与上月的比较
