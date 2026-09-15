## Purpose

自进化闭环的编排与触发层：把质量信号转化为学习运行（evolution run），管理 run 生命周期、lineage 与归因成本预算，并度量已发布 learned skill 的使用与效果回流。它是闭环的骨架，不包含归因算法与验证门禁本身的逻辑。

## Requirements

### Requirement: 质量信号触发轨迹采集
系统 SHALL 在会话完成事件后异步检查学习信号：会话质量分（instruction_adherence、helpfulness）低于配置阈值，或会话中出现用户显式纠正信号时，将该会话登记为学习输入（引用 EventStore 轨迹、evidence 快照与质量分记录）。采集 SHALL 不阻塞会话响应路径，采集失败降级为告警日志。

#### Scenario: 质量分低于阈值触发登记
- **WHEN** 一个会话完成且其 instruction_adherence 均分低于配置阈值（默认 0.6）
- **THEN** 系统异步创建一条学习输入记录，引用该会话的轨迹、evidence 与质量分，状态为 pending

#### Scenario: 用户显式纠正触发登记
- **WHEN** 一个会话完成且包含用户显式纠正（明确否定并要求重做的用户消息）
- **THEN** 即使质量分达标，系统也 SHALL 登记该会话为学习输入

#### Scenario: 正常会话不登记
- **WHEN** 一个会话完成、质量分达标且无显式纠正
- **THEN** 系统不创建学习输入记录

#### Scenario: 采集失败不影响会话
- **WHEN** 采集过程中查询轨迹或写库失败
- **THEN** 系统 SHALL 记录告警日志并继续，会话响应不受影响

### Requirement: 调度式批量归因运行
系统 SHALL 向 MetaAgentScheduler 注册 evolution agent；每个调度间隔到期时，将积累的 pending 学习输入批量组成一次 evolution run。无积累输入时跳过；单次 run 失败不影响后续调度。

#### Scenario: 有积累输入时创建 run
- **WHEN** 调度间隔到期且存在 pending 学习输入
- **THEN** 系统创建一个 evolution run 并将其纳入 analyzing

#### Scenario: 无输入时跳过
- **WHEN** 调度间隔到期但无 pending 学习输入
- **THEN** 不创建 run，仅记录空转日志

#### Scenario: run 失败不阻塞调度
- **WHEN** 某次 run 执行抛出异常
- **THEN** 该 run 进入 failed 终态，下个间隔的调度照常执行

### Requirement: Run 生命周期与 lineage 审计
evolution run SHALL 具备状态机 pending → analyzing → candidates_ready → gated → published / concluded，异常路径为 failed / cancelled。每次 run 及其产出的每个候选 SHALL 记录完整 lineage：来源轨迹引用、归因结论、验证报告、人审决策与操作者，可供查询。

#### Scenario: 状态正常推进
- **WHEN** run 完成归因与候选生成且候选进入验证门禁
- **THEN** run 状态依次经历 analyzing、candidates_ready、gated，每个状态变更带时间戳

#### Scenario: 失败终态保留原因
- **WHEN** run 在归因阶段因 LLM 调用持续失败而终止
- **THEN** run 进入 failed 状态并保留错误原因，已产生的中间产物保留可查

### Requirement: 归因成本预算
每次 evolution run 的 LLM 调用次数与 token 消耗 SHALL 有可配置上限；达到上限时 run 终止并标记 budget_exceeded，已生成的候选保留进入门禁流程。

#### Scenario: 超限终止
- **WHEN** run 的 LLM token 消耗达到配置上限
- **THEN** run 终止为 budget_exceeded，已完成归因的候选照常进入后续阶段

### Requirement: Workspace 隔离
学习输入、evolution run、候选 skill 及其 lineage SHALL 全部按 workspace 隔离；任何 run SHALL 只消费本 workspace 的轨迹，且只为本 workspace 生成候选。

#### Scenario: 跨 workspace 不复用
- **WHEN** workspace A 中出现失败会话
- **THEN** 只有 workspace A 的 run 会消费该输入，workspace B 的候选与队列不受影响

### Requirement: 效果回流观测
系统 SHALL 记录每个已发布 learned skill 的触发次数与 L2 加载次数，并支持按 skill 查询"使用该 skill 的会话质量分布对比未使用会话"的回流统计。

#### Scenario: 使用统计可查
- **WHEN** 某个 learned skill 发布后在一个评估周期内被多个会话触发
- **THEN** 按该 skill 查询可返回触发次数、L2 加载次数与使用/未使用会话的质量分对比

#### Scenario: 无使用时返回空统计
- **WHEN** 某个 learned skill 发布后从未被触发
- **THEN** 统计返回零触发且不产生质量对比结论
