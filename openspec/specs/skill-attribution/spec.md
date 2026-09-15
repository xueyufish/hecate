## Purpose

从失败轨迹中归因根因并生成结构化的候选 skill（知识型 SKILL.md 内容包）。取代旧规则启发式骨架：规则只做预过滤，归因与候选生成由 LLM 完成，产物为可审、可验证、可回滚的 structured delta。

## Requirements

### Requirement: 规则预过滤
系统 SHALL 先用规则启发式对学习输入做预过滤，标记显著失败模式：执行超时、工具连续报错、循环重复、用户纠正。未命中任何模式的输入 SHALL 被标记 skipped 且不进入 LLM 归因队列。

#### Scenario: 命中模式进入归因
- **WHEN** 学习输入的轨迹中存在工具连续报错
- **THEN** 该输入被标记待归因并进入 LLM 归因队列

#### Scenario: 无模式被跳过
- **WHEN** 学习输入的轨迹未命中任何预过滤模式
- **THEN** 该输入标记 skipped，不消耗归因 LLM 调用

### Requirement: LLM 失败归因（AgentRx 分类法）
系统 SHALL 通过 LLM 将预过滤命中的轨迹归因到 AgentRx 十类失败之一（instruction_adherence、information_invention、invalid_invocation、tool_output_misinterpretation、intent_plan_misalignment、underspecified_intent、unsupported_intent、guardrails_triggered、system_failure、inconclusive），输出置信度、根因描述与证据引用（指向轨迹中具体消息）。归因调用 SHALL 走平台模型路由。

#### Scenario: 工具错误归因
- **WHEN** 轨迹包含参数错误的工具调用及其报错结果
- **THEN** 归因输出 invalid_invocation、置信度、根因描述与至少一条证据引用

#### Scenario: 平台侧故障不产候选
- **WHEN** 失败被归因为 system_failure（平台或模型侧故障，非 agent 行为问题）
- **THEN** 该输入结论为 system_failure 且不生成候选 skill

#### Scenario: 无法归因时不产候选
- **WHEN** LLM 归因结论为 inconclusive 或置信度低于下限
- **THEN** 该输入被标记 inconclusive 且不生成候选 skill

### Requirement: 候选 skill 生成
对可防护的失败类别，系统 SHALL 生成候选 skill：知识型 SKILL.md 内容包，包含 name、明确的触发条件 description、procedure 分区（正确做法）与 guardrails 分区（须避免的失败模式）；每条内容 SHALL 附来源 evidence 引用；对既有主题的更新 SHALL 以 structured delta（新增/修订条目 + 理由）表达而非全文重写。

#### Scenario: 首次生成含双分区
- **WHEN** 一条 invalid_invocation 归因首次生成候选
- **THEN** 候选包含 description、procedure 分区与 guardrails 分区，且每条要点附来源轨迹引用

#### Scenario: 既有主题以 delta 更新
- **WHEN** 新归因结论与既有候选主题一致
- **THEN** 既有候选追加 delta 条目（内容 + 理由 + 新 evidence），版本号递增，而非创建重复候选

#### Scenario: 被排除类别不生成
- **WHEN** 归因结论为 system_failure、inconclusive 或 guardrails_triggered
- **THEN** 不生成候选 skill（guardrails_triggered 属平台既有防护生效，无需学习）

### Requirement: 候选去重合并
同一 workspace 内失败类别与主题相近的候选 SHALL 合并为同一候选并追加 delta；合并决策与依据 SHALL 记录在 lineage 中。

#### Scenario: 同主题合并
- **WHEN** 两条不同轨迹归因出相同失败类别且主题相似
- **THEN** 产出一个候选、两条 evidence 引用与两条 delta 来源，而不是两个候选

### Requirement: 候选内容安全扫描
候选文本在进入验证门禁前 SHALL 通过既有内容扫描/DLP 管线；命中风险的候选 SHALL 标记 blocked 并保留扫描结果，不进入人审队列。

#### Scenario: 含注入模式的候选被拦截
- **WHEN** 候选文本包含提示注入或数据泄露风险模式
- **THEN** 候选标记 blocked，附扫描结论，不出现在审核队列

### Requirement: 候选仅限知识型内容
候选 skill SHALL 只包含 Markdown 文本与资源引用；包含可执行脚本的候选 SHALL 在生成期被拒绝。

#### Scenario: 带脚本的生成被拒绝
- **WHEN** LLM 生成的候选内容包含可执行脚本段
- **THEN** 生成结果被拒绝并记录拒绝原因，候选不进入门禁
