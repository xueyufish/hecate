## ADDED Requirements — 新增需求

### Requirement: 扇出管道模板 — 扇出管道模板
系统 SHALL 包含一个预构建的"扇出管道"编排模板，演示一个研究员代理扇出到多个分析师代理并合并结果的并行处理。

#### Scenario: 扇出模板结构
- **WHEN** 加载扇出管道模板
- **THEN** 图 SHALL 包含 1 个研究员 AGENT 节点、1 个 FAN_OUT 节点、3 个分析师 AGENT 节点（analyst_a、analyst_b、analyst_c）、1 个 MERGE 节点和 1 个汇总 AGENT 节点

#### Scenario: 扇出模板边
- **WHEN** 模板被编译
- **THEN** 边 SHALL 为：researcher→fanout、fanout→[analyst_a、analyst_b、analyst_c]、analyst_*→merge、merge→summarizer、summarizer→__end__

### Requirement: 条件管道模板 — 条件管道模板
系统 SHALL 包含一个预构建的"条件管道"编排模板，演示基于分类的多键条件路由。

#### Scenario: 条件模板结构
- **WHEN** 加载条件管道模板
- **THEN** 图 SHALL 包含 1 个分类器 AGENT 节点、1 个 CONDITION 节点和 3 个专家 AGENT 节点（finance_agent、tech_agent、legal_agent），具有多键条件边路由

#### Scenario: 条件模板路由
- **WHEN** 分类器代理输出一个类别
- **THEN** CONDITION 节点 SHALL 根据类别值路由到匹配的专家

### Requirement: 反思循环模板 — 反思循环模板
系统 SHALL 包含一个预构建的"反思循环"编排模板，演示具有质量检查循环的迭代优化。

#### Scenario: 反思模板结构
- **WHEN** 加载反思循环模板
- **THEN** 图 SHALL 包含 1 个起草 AGENT 节点、1 个评审 AGENT 节点、1 个 CONDITION 节点和 1 个修订 AGENT 节点，从修订节点回到评审节点有一条循环边

#### Scenario: 反思循环迭代
- **WHEN** 评审者判定质量不足
- **THEN** CONDITION 节点 SHALL 路由到修订者，然后路由回评审者进行重新评估

#### Scenario: 反思循环终止
- **WHEN** 评审者判定质量合格
- **THEN** CONDITION 节点 SHALL 路由到 __end__