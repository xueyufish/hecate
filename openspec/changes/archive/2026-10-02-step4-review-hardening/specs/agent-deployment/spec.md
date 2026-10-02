# Spec Delta

## ADDED Requirements

### Requirement: Registration validates tenant ownership and capability standing

登记 MUST 校验 Agent、workspace、组织和负责人的归属，版本 MUST 存在且属于该 Agent。能力快照 MUST 为完整执行契约并与类型、所有权一致；登记调用 MUST NOT 自行提升为 enforced。托管配置 MUST 保留事实来源与时间，缺少条件 MUST 保持 unverified；默认部署 MUST 由数据库约束保证至多一个。审计 MUST 使用真实组织归属。

#### Scenario: Foreign version or organization is rejected
- **WHEN** 版本属于另一个 Agent，或 principal 的组织不属于其 workspace
- **THEN** 登记被拒绝且不写入不一致实体

#### Scenario: Caller cannot promote governance standing
- **WHEN** 登记调用传入 enforced 等级或不足核验信息的托管配置
- **THEN** enforced 被拒绝，缺少完整事实的托管配置保持 unverified

### Requirement: Migration preserves governance and execution records

Step4 迁移回退 MUST NOT 删除部署、运行或审计记录。缺少版本或 principal 的存量 Agent MUST 登记可定位且幂等的待治理记录；回填快照 MUST 可被执行契约解析。

#### Scenario: Destructive schema downgrade is refused
- **WHEN** 对 Step4 表结构或回填执行 downgrade
- **THEN** 迁移明确拒绝，保留全部记录，操作者使用应用回退而不是删除 schema
