# Spec Delta

## ADDED Requirements

### Requirement: 确认锚点刷新

整合对记忆行的 UPDATE 与 SUPERSEDE 应用成功后,SHALL 刷新该记忆(或其 successor)的确认锚点字段(`last_confirmed_at`);在线检索与读取 SHALL NOT 刷新该字段。该锚点为读取侧时间衰减的唯一外生时间源。

#### Scenario: UPDATE 刷新锚点

- **WHEN** 整合对某条 L3 记忆应用 UPDATE 成功
- **THEN** 该记忆的 `last_confirmed_at` 更新为本次整合时间,读取侧衰减时钟随之重置

#### Scenario: 取代的 successor 携带新锚点

- **WHEN** 整合以 SUPERSEDE 用新记忆取代旧记忆
- **THEN** 新记忆的 `last_confirmed_at` 为本次整合时间;被取代行已软删除,其锚点不再参与排序

### Requirement: 候选重要性评分规范

整合候选的 importance 评分 SHALL 使用锚定分档语言(明确定义分值区间对应的语义,如"用户明确陈述的持久偏好"对应高分档、"单次情境性细节"对应低分档),由 LLM 在 planning 阶段产出;输出 SHALL clamp 到 [0,1];候选未提供时 SHALL 取中性值 0.5。

#### Scenario: 锚定分档产出

- **WHEN** 审查窗口包含用户明确陈述的长期偏好候选
- **THEN** planning 对该候选给出高分档 importance,落库值在 [0,1] 内

#### Scenario: 缺省中性

- **WHEN** 某候选的 planning 输出未包含 importance
- **THEN** 该候选以 0.5(中性)落库,检索偏置中映射为 1.0× 乘数

### Requirement: 记忆价值离线评分

整合 SHALL 在触碰记忆行时维护一个离线价值评分,由确认新鲜度(自 `last_confirmed_at` 起的半衰衰减)与访问热度(唯一会话去重计数的对数压缩衰减)加权构成;评分及其分量 SHALL 落库可查。该评分 SHALL NOT 驱动任何删除或驱逐,SHALL NOT 参与在线检索排序;容量驱逐属后续变更。

#### Scenario: 价值分可观测

- **WHEN** 整合触碰某条记忆
- **THEN** 该行的价值评分及其分量(新鲜度、热度)被更新且可查询

#### Scenario: 评分不驱动驱逐

- **WHEN** 某些记忆的价值评分很低
- **THEN** 整合不因此删除、软删除或停用它们;评分仅作为字段存在,供运维查询与后续驱逐机制使用
