# execution-state-log Specification

## Purpose
TBD - created by archiving change event-sourced-state. Update Purpose after archive.
## Requirements
### Requirement: WAL 写入序——事件先于通道写入

PregelRuntime SHALL 在将 superstep 结果 apply 到 ChannelManager **之前**，先以单事务批量 append 该 superstep 的全部事件（批内保序）。append 失败时 SHALL NOT 执行对应的通道写入，SHALL 走节点错误路径。

#### Scenario: superstep 批量 append 先于 apply
- **WHEN** 一个 superstep 的多个 WorkerResult 各自产生 channel_updates
- **THEN** 运行时 SHALL 在单次事务中按结果顺序批量 append 全部 `CHANNEL_WRITE` 事件
- **AND** 全部 append 成功后才执行 `_apply_writes`

#### Scenario: append 失败阻断通道写入
- **WHEN** 批量 append 抛出异常
- **THEN** 对应通道写入 SHALL NOT 发生
- **AND** 该 superstep SHALL 按节点错误路径处理

### Requirement: 提交点与撕裂尾部检测

`STEP_END` 与 `INTERRUPT` 事件 SHALL 作为提交点。事件日志尾部若存在 `CHANNEL_WRITE` 但其后无提交点事件，SHALL 判定为撕裂尾部；恢复时 SHALL 回退到上一个完整提交点，撕裂部分对恢复视为未发生。

#### Scenario: 正常提交
- **WHEN** superstep N 的全部事件 append 完毕且 `STEP_END` 落库
- **THEN** superstep N 对恢复可见

#### Scenario: 崩溃后撕裂尾部回退
- **WHEN** 恢复时发现尾部有 `CHANNEL_WRITE` 但无配对的 `STEP_END`/`INTERRUPT`
- **THEN** 恢复 SHALL 回退到上一个完整提交点
- **AND** 撕裂部分的事件 SHALL 保留在日志中（不删除）且不参与状态重建

### Requirement: CHANNEL_WRITE 携带裁决后完整值

`CHANNEL_WRITE` 事件 payload SHALL 携带冲突裁决**之后**实际落地的值（`channel`、`value`）。被 ConflictResolver 拒绝的写入 SHALL NOT 产生 `CHANNEL_WRITE`，SHALL 产生 `CHANNEL_WRITE_REJECTED` 审计事件；fold SHALL 跳过 `CHANNEL_WRITE_REJECTED`。

#### Scenario: 记录裁决后值
- **WHEN** ConflictResolver 将提案合并为 `final_value` 并写入
- **THEN** `CHANNEL_WRITE` payload 的 `value` SHALL 等于 `final_value`

#### Scenario: 被拒写只产审计事件
- **WHEN** ConflictResolver 返回 `resolved=False`
- **THEN** SHALL 追加 `CHANNEL_WRITE_REJECTED` 事件且 fold 跳过它

### Requirement: fold 机器与 derive_messages 投影

引擎层 SHALL 提供 fold 模块：以 `ChannelBehavior.write()` 作为唯一 fold 函数按序消费事件重建通道状态；`EVICTION` 作为事实事件参与 fold；`derive_messages()` SHALL 返回 state fold 的 messages 通道视图（eviction 已生效）。fold 遇到 `log_schema_version` 旧版本事件 SHALL 停止并将该前缀判定为不可回放。

#### Scenario: 从日志重建状态
- **WHEN** 对一个会话的完整日志执行 fold
- **THEN** 重建的通道状态 SHALL 与活运行时的 `ChannelManager.snapshot()` 等价（非排除通道）

#### Scenario: derive_messages 与 messages 通道一致
- **WHEN** 调用 `derive_messages(session_id)`
- **THEN** 返回值 SHALL 等于该会话 fold 后状态的 `messages` 通道值

#### Scenario: 旧 schema 前缀停止 fold
- **WHEN** fold 遇到 `log_schema_version` 缺失或为旧值的事件
- **THEN** fold SHALL 停止并报告不可回放前缀，SHALL NOT 用旧事件重建状态

### Requirement: LogPolicy 黑名单式通道入日志规则

通道 SHALL 默认入日志。`engine` 层 LogPolicy SHALL 集中注册三类排除：① ephemeral（`_fanout__*`、`_resume_value`）；② 可重建控制通道（`_` / `sys.` 前缀）；③ 已有专属持久化（`_agent_state`）。`_route` SHALL 显式豁免入日志（条件路由属于 fold 正确性的一部分）。

#### Scenario: 新值通道默认入日志
- **WHEN** worker 向未注册于 LogPolicy 的新通道写入
- **THEN** 该写入 SHALL 产生 `CHANNEL_WRITE` 事件

#### Scenario: 控制通道不入日志
- **WHEN** worker 写入 `_tools` 或 `sys.execution_mode`
- **THEN** SHALL NOT 产生 `CHANNEL_WRITE` 事件
- **AND** 恢复时由 execution_service 重新注入初始值

#### Scenario: _route 豁免
- **WHEN** worker 写入 `_route` 通道
- **THEN** 该写入 SHALL 产生 `CHANNEL_WRITE` 事件

### Requirement: LogInvariants 运行时不变式注册表

引擎层 SHALL 提供 LogInvariants 注册表，各引擎模块以伴随模式自注册检查：step 边界配对、工具调用配对平衡（`TOOL_CALL` 无配对 `TOOL_RESULT` 跨提交点即 pending 或失衡报告）、LLM 请求出处（冻结请求中每条消息可在日志中找到产生事件）、dispatch 树一致性（每个 `SUBGRAPH_START` 有配对 `SUBGRAPH_END` 且子日志提交点完整）、投影等价。不变式违背 SHALL 以明确的 invariant error 抛出。

#### Scenario: 工具配对失衡被检出
- **WHEN** fold 时发现 `TOOL_CALL` 在下一个提交点前无配对 `TOOL_RESULT`
- **THEN** invariants SHALL 报告配对失衡

#### Scenario: 出处级检查通过
- **WHEN** 校验某 `LLM_REQUEST` 冻结请求
- **THEN** 请求中每条消息 SHALL 能追溯到日志中的产生事件

### Requirement: 投影等价断言 fail-stop

在恢复、interrupt、物化三类边界，运行时 SHALL 校验 `projection(log) ≡ ChannelManager.snapshot()`（仅非排除通道）。断言失败时 SHALL fail-stop（抛 invariant error 并中止本次执行），SHALL NOT 以日志热修复运行中状态。

#### Scenario: 断言失败即中止
- **WHEN** 恢复时 fold 结果与缓存投影不一致
- **THEN** 执行 SHALL 以 invariant error 中止
- **AND** 下次恢复 SHALL 直接从日志全量 fold

### Requirement: 子图嵌套 session 日志

子图/子代理执行 SHALL 使用独立 session_id 的事件日志；父日志 SHALL 记录 `SUBGRAPH_START {child_session_id}` / `SUBGRAPH_END {child_session_id}` 引用对。子会话 events 行的 `org_id`/`user_id` SHALL 继承父会话。

#### Scenario: 子图日志独立
- **WHEN** agent_worker 派发子代理执行
- **THEN** 子代理事件写入子 session 日志，父日志仅含 START/END 引用对

#### Scenario: 父 fold 零过滤
- **WHEN** fold 父会话日志重建状态
- **THEN** SHALL 无需任何 scope/前缀过滤

### Requirement: Resume 依据日志推导而非 Session 行

resume 端点 SHALL 以日志为校验依据：会话事件尾部存在未闭合 `INTERRUPT` 即允许恢复。Session 行缺失时 SHALL 懒补建。恢复流程 SHALL 为：加载 SessionState 缓存 → 撕裂检测/回退 → tail 重放到提交点 → 重建 runtime → 注入 `resume_value` → 续跑。graph 重新编译失败 SHALL 拒绝恢复（fail-fast）。

#### Scenario: 无 Session 行的 chat 会话可恢复
- **WHEN** 会话仅有 events 无 SessionModel 行，且尾部存在未闭合 `INTERRUPT`
- **THEN** resume SHALL 正常执行恢复并懒补建 Session 行

#### Scenario: graph 已变更则拒绝恢复
- **WHEN** 恢复时 workflow graph 重新编译失败
- **THEN** resume SHALL 返回明确错误而非静默降级

### Requirement: Session.status 为日志投影

`SessionModel.status` SHALL 仅由真实事件驱动变更（INTERRUPT 置 interrupted、RESUME/续跑完成置回），SHALL NOT 存在绕过日志直接写 status 的路径。

#### Scenario: status 与日志同步
- **WHEN** 引擎产生 `INTERRUPT` 事件
- **THEN** 对应会话的 status SHALL 变为 `interrupted`

### Requirement: LLM_REQUEST 记录完整冻结请求

`LLM_REQUEST` 事件 payload SHALL 记录 context assembly 之后实际发往 provider 的完整冻结请求（消息数组、tools、参数），超大载荷 SHALL 过有界保留器（截断 + `omitted` 标记 + 原始值可经环境/offload 取回）。流式 chunk SHALL NOT 入日志；聚合后的 `LLM_RESPONSE` SHALL 入日志。

#### Scenario: 冻结请求可回放
- **WHEN** 读取一次 LLM 调用的 `LLM_REQUEST` 事件
- **THEN** payload SHALL 包含实际发出的完整消息数组（或其截断 + omitted 标记版本）

#### Scenario: 流式 chunk 不入日志
- **WHEN** MESSAGES 流式模式产生中间 chunk
- **THEN** 日志中 SHALL 只有聚合后的 `LLM_RESPONSE`，无逐 chunk 事件

### Requirement: 物化缓存语义与回退

物化缓存（经 `CheckpointStore` 缝 + adapter 落 SessionStateStore）SHALL 仅承载优化职责：载荷为 `channel_state`（有界投影）+ `log_version`（复用 `SessionState.event_position`）。缓存缺失、过期或不完整时 SHALL 回退 tail 重放或全量 fold，SHALL NOT 阻塞恢复。

#### Scenario: 缓存烂了不碍事
- **WHEN** SessionState 缓存的 channel_state 与日志校验不一致
- **THEN** 恢复 SHALL 以日志为准重建并继续

