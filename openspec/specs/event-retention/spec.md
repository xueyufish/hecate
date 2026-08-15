# event-retention Specification

## Purpose
TBD - created by archiving change event-sourced-state. Update Purpose after archive.
## Requirements
### Requirement: 会话级 TTL 从终态起算

事件 retention SHALL 以会话（树）为删除单位、以会话**终态时间**（completed/failed/expired）为计时起点。`interrupted` 状态的会话 SHALL 豁免于自动清理。默认时长 SHALL 为 conversational 30 天 / task 7 天，org 级配置可覆盖。

#### Scenario: 终态后才计 TTL
- **WHEN** 会话于 T 日进入 completed 状态，TTL 为 30 天
- **THEN** 该会话的日志最早在 T+30 日被清理

#### Scenario: interrupted 豁免
- **WHEN** 会话处于 interrupted 状态超过 TTL 时长
- **THEN** 其日志 SHALL NOT 被自动清理

### Requirement: 会话树级联删除

删除会话 SHALL 级联删除：该会话及全部子会话的 events 行、对应 SessionState 缓存、Session 行。SHALL NOT 对单一会话日志做部分修剪（fold-from-origin 要求前缀完整）。

#### Scenario: 父会话删除带走子会话
- **WHEN** 含子代理会话的父会话被清理
- **THEN** 子会话的 events 行 SHALL 一并删除

### Requirement: conversation 关联对象级联清单

删除 conversation SHALL 级联删除：Conversation、Message、TurnScore、关联 Evidence、Cluster 归属。级联清单 SHALL 在 retention 实现中以单一权威清单维护。

#### Scenario: conversation 删除完整级联
- **WHEN** 一个含消息、评分、证据的 conversation 被删除
- **THEN** 清单中全部关联对象 SHALL 被删除，无孤儿行残留

### Requirement: GDPR 级联与 PIIMapping 同生共死

GDPR/租户删除 SHALL 以 `org_id`/`user_id` 列扫描全部会话相关表。`PIIMapping` SHALL 与该租户的 events 同批删除（同生共死）；事件日志 SHALL 只存掩码占位符，加密原文 SHALL 仅存于 PIIMapping。

#### Scenario: 租户删除全清
- **WHEN** 某租户发起 GDPR 删除
- **THEN** events、SessionState、PIIMapping 及全部关联对象按清单删除

### Requirement: 写入时有界载荷保留器

进入事件日志的大载荷（LLM_REQUEST 冻结请求、工具结果）SHALL 过有界保留器：head/tail 字节预算、UTF-8 边界安全、精确 `omitted_bytes` 标记、恢复指引。SHALL 优先于事后清扫（控增长率优先于控表大小）。

#### Scenario: 超大工具结果被有界保留
- **WHEN** 工具返回 2MB 输出且预算为 head 32KB + tail 8KB
- **THEN** 日志中的载荷 SHALL ≤ 预算 + 标记，含精确 `omitted_bytes`

### Requirement: 单会话 warn-only 阈值

单会话日志 SHALL 设 warn-only 阈值（默认 ~10MB / 10k 事件），触达时 SHALL 记录 metric 与日志告警，SHALL NOT 强制终止执行或截断日志。

#### Scenario: 阈值触达仅告警
- **WHEN** 会话日志体积越过阈值
- **THEN** 产生告警 metric，执行继续不受影响

### Requirement: 安全删除机制

清理任务 SHALL：定时低峰执行、游标分页 `(created_at, id)`、批大小上限、dry-run 模式、执行指标上报（批次/扫描/删除计数）。

#### Scenario: dry-run 不删数据
- **WHEN** 清理任务以 dry-run 运行
- **THEN** 仅输出将删除的统计，无行被删除

#### Scenario: 批删不锁表
- **WHEN** 清理任务处理大量表
- **THEN** 每批删除受批大小上限约束并游标推进

### Requirement: 策略枚举 delete|archive

retention 策略 SHALL 为 `delete | archive` 枚举；本 change 仅实现 `delete`，`archive` 为预留位（S3/MinIO 冷归档），配置校验 SHALL 接受枚举值。

#### Scenario: 默认策略为 delete
- **WHEN** 未配置策略
- **THEN** 生效策略为 `delete`

#### Scenario: archive 值被接受但不实现
- **WHEN** 策略配置为 `archive`
- **THEN** 配置校验通过，实现按 `delete` 行为执行并记录 not-implemented 日志

