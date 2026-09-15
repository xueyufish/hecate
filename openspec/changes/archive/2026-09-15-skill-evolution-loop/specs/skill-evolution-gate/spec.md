## Purpose

候选 skill 的验证门禁与人工发布层：任何学习产物在生效前必须通过四项验证（回归、不退化、触发、基线对比）并经人工审核；发布携带完整 provenance，支持下架回滚。它是闭环的治理闸门——自动生成的产物默认不可信。

## ADDED Requirements

### Requirement: 四项验证套件
候选 skill 进入人审队列前 SHALL 完成验证并产出结构化报告：(a) 数据集回归——在本 workspace 选定的命名数据集版本上运行评估，结果不低于基线阈值；(b) golden subset 不退化——在该 agent 历史轨迹固化的黄金子集上，指标不得劣于不开启候选时的基线；(c) 触发测试——在相关历史轨迹上候选应被触发进入 L2 加载，在无关轨迹上不应被触发；(d) with/without baseline——开启候选后相关样本的评估指标应有改善。报告 SHALL 逐项记录 pass/fail/skipped 与证据数据。

#### Scenario: 全部通过进入人审
- **WHEN** 四项验证全部 pass
- **THEN** 候选进入人审队列，报告附于候选详情

#### Scenario: 任一项 fail 阻断
- **WHEN** 验证中 golden subset 出现指标退化
- **THEN** 候选标记验证未通过，不进入人审队列，报告保留失败数据

#### Scenario: 数据不足挂起
- **WHEN** golden subset 或触发测试所需的该 agent 历史轨迹不足
- **THEN** 候选标记 insufficient_data 并挂起，补充数据后可重新验证

### Requirement: 人工审核与发布
验证通过的候选 SHALL 经人工审核；审核者可批准、驳回（附原因）或修改后批准。批准后 skill 以 published 状态写入本 workspace 的 skill registry，并携带 provenance：来源 run、失败类别、验证报告、编辑 diff 与审核者身份。

#### Scenario: 批准发布
- **WHEN** 审核者批准一个验证通过的候选
- **THEN** 对应 skill 在该 workspace 注册为 published，provenance 完整，可被 agent 绑定

#### Scenario: 驳回终态
- **WHEN** 审核者驳回候选并填写原因
- **THEN** 候选进入 rejected 终态，lineage 保留，不影响同主题其他候选

#### Scenario: 修改后批准留痕
- **WHEN** 审核者编辑候选内容后批准
- **THEN** 发布的 skill 记录编辑前后的 diff，provenance 标注 modified_by_reviewer

### Requirement: 已发布 skill 的下架回滚
已发布的 learned skill SHALL 可被下架（unpublish）；下架后不再出现在任何 agent 的 L1 目录、不再可被 L2 加载；其 lineage 与历史使用统计 SHALL 保留可查。

#### Scenario: 下架后立即失效
- **WHEN** 审核者下架一个已发布的 learned skill
- **THEN** 后续新会话的 L1 目录不包含该 skill，L2 加载请求返回不可用

### Requirement: Workspace 隔离的审核队列
候选与审核队列 SHALL 按 workspace 隔离；审核者 SHALL 只能看到本 workspace 的候选。已发布 learned skill 遵循既有 skill 绑定模型（agent 显式绑定或 auto_load）。

#### Scenario: 审核者只见本 workspace 候选
- **WHEN** workspace B 的审核者打开候选列表
- **THEN** 列表中不出现 workspace A 的任何候选

### Requirement: 审核 API
系统 SHALL 提供 studio API：候选列表与详情、验证报告查询、批准/驳回/修改后批准、lineage 查询（轨迹 → 归因 → 验证 → 审核全链引用）。

#### Scenario: lineage 全链可查
- **WHEN** 查询某个已发布 learned skill 的 lineage
- **THEN** 返回来源轨迹引用、归因结论与证据、验证报告、审核记录的完整链路
