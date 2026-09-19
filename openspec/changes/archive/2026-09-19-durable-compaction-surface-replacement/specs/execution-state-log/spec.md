## ADDED Requirements

### Requirement: 压缩 bracket 事件入日志

执行日志 SHALL 以 additive 方式新增四个事件类型：`COMPACTION_STARTED`（`trigger`、`threshold_snapshot`、`compaction_id`）、`COMPACTION_SUMMARY`（`compaction_id`、结构化 `summary` 节点、`shadowed_seqs` 含头含尾区间、`usage`、`prior_compaction_id`）、`CONTEXT_SURFACE_REPLACED`（`compaction_id`、`start_seq`、`end_seq`、`start_anchor`、`end_anchor`——区间端点消息的内容哈希）、`COMPACTION_COMPLETED`（`compaction_id`、`tokens_before`、`tokens_after`）。`threshold_snapshot` 的 token 值与 `tokens_before`/`tokens_after` SHALL 以有效 surface（影子化视图之后、预算裁剪之前）计量。

四个压缩事件 SHALL 全部为 bookkeeping 事件：fold 机器 SHALL 跳过它们，通道状态不受压缩影响；它们 SHALL NOT 携带 `log_schema_version` 标记，SHALL NOT 触发不可回放前缀判定。压缩事件 SHALL 经由与既有 bookkeeping 事件相同的 append 路径写入日志；压缩 SHALL NOT 导致任何既有事件被删除或改写——被遮蔽的原始消息在其 `CHANNEL_WRITE` 事件中永久可查。相邻相同内容的消息在下标平移后恰好使端点哈希仍然吻合属已知限制：后果有界（下次压缩前隐藏范围偏移至多若干条），且由后续压缩自动纠正。

#### Scenario: fold 跳过压缩事件

- **WHEN** 对包含完整压缩 bracket 的会话日志执行 fold
- **THEN** 重建的通道状态 SHALL 与不含压缩事件时的 fold 结果完全一致（messages 通道保持全量原文）

#### Scenario: 旧读者安全降级

- **WHEN** 不认识压缩事件类型的既有日志读者遇到四个压缩事件之一
- **THEN** 该读者 SHALL 按既有 unknown-type 回退规则（CUSTOM 语义）处理，SHALL NOT 报错或误读载荷

#### Scenario: 原文永久可查

- **WHEN** 一次压缩完成后查询被遮蔽区间内消息的产生事件
- **THEN** 对应的 `CHANNEL_WRITE` 事件 SHALL 仍然存在且载荷未被改动

#### Scenario: 崩溃遗留孤儿 START 可见

- **WHEN** 压缩在 `COMPACTION_STARTED` 之后、`COMPACTION_COMPLETED` 之前崩溃
- **THEN** 日志 SHALL 保留该未闭合 bracket（可见、可审计），SHALL NOT 存在伪造的摘要或替换事件
