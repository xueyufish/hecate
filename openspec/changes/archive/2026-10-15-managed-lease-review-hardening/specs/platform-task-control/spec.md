# Spec Delta

## ADDED Requirements

### Requirement: 位置修复与竞争去重保留真实事件事实

事件位置唯一性 SHALL 同样覆盖无 workspace 的 Run。升级历史同位事件 SHALL 使用真实创建时间确定保留顺序并以 UUID 作确定性平局排序，比较语义 MUST 在 PostgreSQL 与 SQLite 成立。并发重用 event_id 时，只有相同 workspace 与相同事件事实 SHALL 视为幂等；内容或归属不同 MUST 显式拒绝，不能使原事件或终态投影被竞争输入覆盖。

#### Scenario: UUID 重复位置升级

- **WHEN** PostgreSQL 上两行 UUID 大小顺序与创建时间顺序相反
- **THEN** 升级保留创建较早记录并报告修复，空 workspace 也只能存一行同位置事件

#### Scenario: 同 event_id 异体竞争

- **WHEN** 两个写入请求并发使用同 event_id 但不同 payload 或 workspace
- **THEN** 后者得到显式冲突，原始事实与投影不被替换
