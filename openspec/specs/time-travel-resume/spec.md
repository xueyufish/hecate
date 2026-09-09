## Purpose

时间旅行恢复能力:把"从任意历史提交点继续执行"(fork-and-run)与"追加记录式状态修改"(update_state)作为多租户平台特性交付。事件日志是唯一事实源,提交点从日志派生,不引入 checkpoint 历史存储;一切修改以可审计的追加事件落日志。

## Requirements

### Requirement: fork 跨越扇出窗口的计划与输出保真

fork 的锚点选择 SHALL 覆盖扇出提交点:`commit-points` 列举 SHALL 含扇出 superstep 的 `STEP_END` 锚点(无需新增事件类型);在扇出提交点(计划已提交、MERGE 未执行)fork 时,子会话的 `FORK` 载荷 SHALL 携带派发计划与全部分支输出,推导续跑集(`next_nodes`)SHALL 为计划的调用列表而非静态出边——子会话从 fork 点继续 SHALL 重现父会话的扇出执行(分支已在父会话完成的,子会话 SHALL NOT 重复派发)。

#### Scenario: 从扇出提交点 fork 后继续
- **WHEN** 在动态扇出 superstep 已提交、分支未派发的日志版本 fork
- **THEN** 子会话 SHALL 以计划中的 5 个调用为续跑集,SHALL NOT 回退到 planner 的静态出边

#### Scenario: 分支完成后 fork 不重复派发
- **WHEN** 在分支全部执行完毕、MERGE 未执行的日志版本 fork
- **THEN** 子会话续跑集 SHALL 为 MERGE(分支已在其折叠态中),SHALL NOT 再次派发分支调用

### Requirement: commit-points 列举呈现扇出信息

`commit-points` 列举对扇出 superstep 的锚点 SHALL 附带可辨识元数据(如计划包数 / 扇出源节点),使 8.20 回放与 what-if 消费方能定位扇出窗口,SHALL NOT 要求消费方自行 fold 全日志推断。

#### Scenario: 扇出锚点可定位
- **WHEN** 会话在某 superstep 执行了 5 包动态扇出
- **THEN** 该 superstep 的 `STEP_END` 锚点 SHALL 携带扇出元数据(源节点与包数)


### Requirement: 提交点锚点列举 API

系统 SHALL 提供 `GET /api/sessions/{id}/commit-points`,从目标会话的事件日志派生可恢复锚点列表。每个锚点 SHALL 包含:`log_version`、类别(`STEP_END`、`INTERRUPT` 或 `FORK`,后者仅子会话的引导锚点)、`node_id`、`superstep`、时间戳;列表 SHALL 按 `log_version` 降序返回,支持 `limit` 参数(默认 20)。锚点集合 SHALL 完全由日志派生,SHALL NOT 依赖 checkpoint 缓存。访问 SHALL 受 workspace 隔离约束,跨 workspace 访问返回 404;无日志或日志为空的会话 SHALL 返回空列表。

#### Scenario: 正常列举
- **WHEN** 一个会话执行过 3 个 superstep 且第 2 步后发生中断
- **THEN** 响应 SHALL 包含按版本降序的锚点列表,其中至少含 2 个 `STEP_END` 锚点与 1 个 `INTERRUPT` 锚点,各带其 `log_version` 与 `superstep`

#### Scenario: checkpoint 缓存缺失不影响结果
- **WHEN** 同一会话的物化 checkpoint 缓存被清空后再次请求
- **THEN** 锚点列表 SHALL 与缓存存在时完全一致

#### Scenario: 空日志
- **WHEN** 会话存在但事件日志为空
- **THEN** 响应 SHALL 返回空列表且 HTTP 200

#### Scenario: workspace 隔离
- **WHEN** 调用方 workspace 与会话所属 workspace 不一致
- **THEN** 响应 SHALL 为 404

### Requirement: fork-and-run——从历史提交点创建子会话续跑

系统 SHALL 提供 `POST /api/sessions/{id}/fork`,请求体含 `at_version`(目标日志版本)与可选 `updates`(channel 到值的映射)。fork SHALL 满足:

1. **锚点对齐**:`at_version` 非 `STEP_END`/`INTERRUPT` 提交点时,SHALL 回退到不大于它的最近提交点,响应中返回实际生效的 `effective_version`。
2. **原始会话不可变**:fork SHALL NOT 向父会话日志追加、修改或删除任何事件,SHALL NOT 修改父会话行状态。
3. **子会话自包含**:SHALL 创建新 session 行,其日志首事件为 `FORK` 快照事件,携带父会话 lineage(`parent_session_id`、`parent_log_version`)、折叠后的通道状态(排除下划线与 `sys.` 前缀的临时通道)与续跑节点集;子会话日志 SHALL 能独立重建子会话状态(不依赖读取父日志)。
4. **续跑**:SHALL 以快照态为初始状态、从续跑节点集继续执行 superstep 循环;`updates` 若提供,SHALL 作为带 `source="update_state"` 标记的日志写入先于执行落盘。
5. **响应**:SHALL 返回子会话完整数据(含 lineage)与执行结果流或最终状态,并显式携带副作用重执行声明字段。

#### Scenario: 从中断点 fork 并改状态
- **WHEN** 会话在节点 N 前中断,调用方以中断锚点版本 fork 且 `updates={"draft": "修订后的计划"}`
- **THEN** 子会话 SHALL 被创建并立即从被暂停节点集合继续执行,`draft` 通道在执行开始前已按 reducer 语义更新,且子日志中的对应 `CHANNEL_WRITE` 事件携带 `source="update_state"`

#### Scenario: 非提交点版本向下对齐
- **WHEN** `at_version` 落在某 superstep 的中间写入处
- **THEN** 系统 SHALL 采用不大于该版本的最近提交点,并在响应 `effective_version` 中如实返回

#### Scenario: 父会话零侵入
- **WHEN** fork 成功完成后读取父会话日志
- **THEN** 父日志的事件数与内容 SHALL 与 fork 前逐字节一致,父会话行状态不变

#### Scenario: 子会话独立可恢复
- **WHEN** 子会话执行若干 superstep 后进程崩溃并恢复
- **THEN** 仅凭子会话自身日志 SHALL 完成状态重建与投影等价校验

#### Scenario: 同源多次 fork
- **WHEN** 对同一父会话同一锚点连续 fork 两次且 `updates` 不同
- **THEN** SHALL 产生两个独立子会话,各自执行互不影响,父会话不受影响

#### Scenario: 无效锚点
- **WHEN** `at_version` 大于当前日志版本或会话不存在
- **THEN** 响应 SHALL 分别为 422/404

### Requirement: 副作用重执行如实声明

fork 续跑 SHALL NOT 回滚、去重或补偿锚点之前已执行的外部副作用(工具调用、消息发送等);通道状态按日志折叠恢复,副作用执行的"物理世界结果"不回退。fork API 响应与 OpenAPI 描述 SHALL 显式声明该语义。

#### Scenario: 副作用不回滚
- **WHEN** 锚点前某工具节点已发送外部通知,从更早锚点 fork 续跑再次经过该节点
- **THEN** 该工具 SHALL 被再次执行(通知再次发送),系统 SHALL NOT 尝试撤销第一次发送

#### Scenario: 响应携带声明
- **WHEN** fork 请求成功
- **THEN** 响应 SHALL 包含声明副作用将重执行的字段(非空说明文本)

### Requirement: update_state——追加记录式状态修改

系统 SHALL 提供 `POST /api/sessions/{id}/state`,请求体含 `values`(channel 到值的映射)。修改 SHALL 满足:

1. **追加记录**:每个 channel 的修改 SHALL 以一条 `CHANNEL_WRITE` 事件落日志,payload 携带 `source="update_state"` 与发起者身份(`actor`);SHALL NOT 原地改写任何既有事件。
2. **reducer 语义**:修改值 SHALL 经目标 channel 的写入行为应用(累加型 channel 追加、覆盖型 channel 替换),与执行期写入同一路径;被 LogPolicy 排除的临时通道 SHALL 被拒绝或忽略(不可作为修改目标落日志)。
3. **门控**:会话日志存在未闭合 TURN 且其后无 `ERROR` 事件(执行中)时 SHALL 返回 409;中断态、空闲态与已崩溃(未闭合 TURN 后跟 `ERROR`)SHALL 允许。
4. **响应**:SHALL 返回修改落盘后按日志折叠得到的完整通道状态与新的 `log_version`。
5. **组合**:对中断态会话的修改 SHALL NOT 关闭其中断(未闭合 `INTERRUPT` 语义保持),后续 resume 依据 ① 的中断推导照常工作。

#### Scenario: 中断态改值后恢复
- **WHEN** 会话处于中断态,调用方修改 `messages` 之外的某业务通道后调用既有 resume
- **THEN** 修改值 SHALL 出现在恢复后的通道状态中,且 resume 的续跑节点推导与未修改时一致

#### Scenario: 累加型通道按追加生效
- **WHEN** 对 `messages` 通道提交一条新消息作为 `values`
- **THEN** 折叠后的消息列表 SHALL 为原列表追加新消息,而非替换

#### Scenario: 执行中拒绝
- **WHEN** 会话日志最后一个 `TURN_START` 之后没有 `TURN_END` 且其后也没有 `ERROR` 事件(执行中)
- **THEN** 响应 SHALL 为 409 且日志无新增事件

#### Scenario: 已崩溃会话放行
- **WHEN** 未闭合 `TURN_START` 之后存在 `ERROR` 事件(执行已崩溃)
- **THEN** 修改 SHALL 被接受

#### Scenario: 审计可查
- **WHEN** 修改完成后读取会话事件日志
- **THEN** 每个被修改 channel SHALL 恰好新增一条 `CHANNEL_WRITE` 事件,payload 含 `source="update_state"` 与 `actor`,既有事件无任何变化

#### Scenario: workspace 隔离与不存在
- **WHEN** 跨 workspace 访问或会话不存在
- **THEN** 响应 SHALL 为 404

### Requirement: fork lineage 可追溯

子会话 SHALL 在会话数据中暴露 `parent_session_id` 与 `parent_log_version`;`GET /api/sessions` SHALL 支持按 `parent_session_id` 过滤以列举同一父会话的全部 what-if 分支。

#### Scenario: 列举同源分支
- **WHEN** 对父会话 P fork 出 3 个子会话后以 `parent_session_id=P` 查询会话列表
- **THEN** 结果 SHALL 恰好包含这 3 个子会话且不含 P 自身

#### Scenario: 会话详情含 lineage
- **WHEN** 读取任一子会话详情
- **THEN** 响应 SHALL 含其 `parent_session_id` 与 `parent_log_version`;非 fork 会话这两个字段 SHALL 为空
