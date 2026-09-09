## MODIFIED Requirements

### Requirement: 提交点与撕裂尾部检测

`STEP_END`、`INTERRUPT` 与 `FORK` 事件 SHALL 作为提交点。事件日志尾部若存在 `CHANNEL_WRITE` 但其后无提交点事件,SHALL 判定为撕裂尾部;恢复时 SHALL 回退到上一个完整提交点,撕裂部分对恢复视为未发生。

#### Scenario: 正常提交
- **WHEN** superstep N 的全部事件 append 完毕且 `STEP_END` 落库
- **THEN** superstep N 对恢复可见

#### Scenario: 崩溃后撕裂尾部回退
- **WHEN** 恢复时发现尾部有 `CHANNEL_WRITE` 但无配对的 `STEP_END`/`INTERRUPT`/`FORK`
- **THEN** 恢复 SHALL 回退到上一个完整提交点

#### Scenario: FORK 是提交点
- **WHEN** 子会话日志为 `[FORK, …CHANNEL_WRITE…]` 且尾部 `CHANNEL_WRITE` 后无后续提交点
- **THEN** 撕裂检测 SHALL 回退到 `FORK` 所在版本,`FORK` 携带的快照对恢复可见

## ADDED Requirements

### Requirement: FORK 快照事件的载荷与 fold 语义

`FORK` 事件 payload SHALL 携带:`parent_session_id`、`parent_log_version`(lineage)、`channel_state`(折叠态投影,排除下划线前缀与 `sys.` 前缀通道)、`next_nodes`(续跑节点集)、`superstep`、`log_schema_version`。fold 机器遇到 `FORK` 时 SHALL 以 payload 的 `channel_state` 水合通道(对涉及的通道整体恢复),其后的 `CHANNEL_WRITE` SHALL 在水合结果上增量应用;`FORK` 之后的投影等价断言与 LogInvariants 校验 SHALL 照常执行。fold 对 `INTERRUPT` 与 `FORK` 之外的语义保持既有行为。

#### Scenario: 子会话日志独立折叠
- **WHEN** 对子会话日志 `[FORK(state), CHANNEL_WRITE(x,1), STEP_END]` 全量折叠
- **THEN** 结果 SHALL 等于 `FORK.channel_state` 叠加 `x=1` 后的状态

#### Scenario: 水合替换而非合并
- **WHEN** `FORK.channel_state` 中某通道值与(不存在于子日志的)父日志历史不同
- **THEN** 折叠结果 SHALL 取 `FORK.channel_state` 的值(快照是子会话事实源,不回读父日志)

### Requirement: initial_input 写入入日志

冷启动 `initial_input` 中通过 LogPolicy 的通道 SHALL 以 `CHANNEL_WRITE` 事件落日志(payload 含 channel/value/log_schema_version),并以 `STEP_END(source="initial_input")` 作为提交点收尾,随后才 apply 到通道(遵循 WAL 序)。被 LogPolicy 排除的控制通道保持仅内存注入。该修复使 fold(log) 与 live 状态对用户输入一致——投影等价不再空转。

#### Scenario: 用户消息进日志
- **WHEN** 冷启动以 `initial_input={"messages": ["hi"], "_session_id": "…"}` 执行
- **THEN** 日志 SHALL 含 messages 的 CHANNEL_WRITE 与 `STEP_END(source="initial_input")`,且不含 `_session_id` 写入

#### Scenario: 折叠含用户输入
- **WHEN** 对含 initial_input 批次的日志全量折叠
- **THEN** messages 通道 SHALL 包含 "hi"

### Requirement: update_state 写入遵循 WAL 序、裁决路径与提交点收尾

状态修改写入 SHALL 与执行期写入走相同落日志路径:每 channel 一条 `CHANNEL_WRITE` 事件,payload 携带 `channel`、`value`、`log_schema_version`、`source="update_state"`、`actor`;写入 SHALL 先 append 后应用(与 WAL 写入序一致);`_route` 等需入日志通道的既有规则 SHALL 不变。修改 SHALL NOT 产生绕过 LogPolicy 的通道写入。修改批次 SHALL 以一条携带 `source="update_state"` 标记的 `STEP_END` 作为提交点收尾,使恢复路径不将其判定为撕裂尾部。

#### Scenario: 修改事件与执行事件同构
- **WHEN** 对比 update_state 产生的 `CHANNEL_WRITE` 与节点执行产生的 `CHANNEL_WRITE`
- **THEN** 两者 payload 结构 SHALL 一致,差异仅在 `source`/`actor` 标记

#### Scenario: 修改批次有提交点收尾
- **WHEN** update_state 落盘后检查日志尾部
- **THEN** 最后一条修改 `CHANNEL_WRITE` 之后 SHALL 存在带 `source="update_state"` 的 `STEP_END` 提交点

#### Scenario: 修改不破坏投影等价
- **WHEN** update_state 落盘后执行恢复路径的投影等价校验
- **THEN** 校验 SHALL 通过(缓存折叠与日志折叠一致,修改写入不被撕裂尾部规则丢弃)
