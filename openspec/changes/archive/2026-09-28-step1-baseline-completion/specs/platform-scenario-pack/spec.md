# platform-scenario-pack Delta

## ADDED Requirements

### Requirement: Independent review chain scenario

场景包 MUST 提供完整验收链场景:读取材料 → 产出摘要 → 独立复核 → 人工批准 → 写入测试工单。复核 MUST 由独立于起草 Agent 的第二主体(独立 Agent 登记与独立会话)执行,其输入只含草稿与复核指令;复核未通过时 MUST NOT 产生工单副作用,也 MUST NOT 产生审批事件;复核通过后的写入 MUST 经审批门禁。断言 MUST 使用确定性断言:材料读取次数、复核者实际收到草稿文本、主体身份区分、审批事件顺序与副作用计数;内容质量复核属 Tier 2/step10 的 rubric 范围,MUST NOT 在本场景中以平均分替代。

#### Scenario: Review rejection blocks the write

- **WHEN** 复核主体否决草稿
- **THEN** 工单服务零副作用,且不产生任何审批事件

#### Scenario: Approved review leads to exactly one write

- **WHEN** 复核通过且人工批准
- **THEN** 工单服务恰被执行一次,APPROVAL_ASKED 与 APPROVAL_DECIDED 成对出现且先于写入,最终答复引用真实工单标识

### Requirement: Cost baseline fields in Tier-2 records

Tier 2 记录产物 MUST 携带成本基线字段块,显式声明采集状态、未采集原因、对应门禁与采集时的字段清单(token 分类、usage 来源 reported/estimated、价格版本、货币与金额、延迟)。确定性 rubric 运行 MUST 将采集状态标为未采集并写明"无模型调用"原因,不得记为零成本。以真实模型运行并采集用量时,MUST 逐字段区分 reported 与 estimated 并固定价格版本,不得将估算标记为最终成本;真实成本基线的可用性 MUST 以 G4(reported/estimated/reconciled 用量记账)关闭为前置。

#### Scenario: Deterministic run records no-cost state

- **WHEN** 运行确定性 rubric 基线并生成记录
- **THEN** 记录的 cost 块状态为未采集,写明原因与门禁指向,不出现零成本数值

#### Scenario: Model-backed run records usage provenance

- **WHEN** 以真实模型运行基线并采集用量
- **THEN** 记录逐字段区分 reported/estimated 并固定价格版本,估算不标记为最终成本
