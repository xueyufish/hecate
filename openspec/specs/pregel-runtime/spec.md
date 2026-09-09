## Purpose

The Pregel runtime executes compiled graphs as **superstep cycles**: each round reads channels, dispatches scheduled nodes concurrently, applies writes, persists checkpoint state, and yields stream events until termination or interrupt. It is the engine layer that orchestrates graph execution and integrates with the event log (log-as-truth in 1.3.19).

## Requirements

### Requirement: 派发单元为调用列表

引擎的 superstep 调度单元 SHALL 从节点 ID 集合升级为**调用列表**(invocation:目标节点 + 调用身份 + 载荷引用)。同一目标节点 SHALL 允许在同一 superstep 内并行多次调用;对调用列表 SHALL NOT 做节点 ID 去重(既有静态路径的节点集语义保持不变)。扇出派发的 NODE_START / NODE_END 事件 payload SHALL 携带调用身份(源节点 + 分支索引),使日志与流式消费者能区分同节点的并行调用。

#### Scenario: 同节点同 superstep 并行多次调用
- **WHEN** 派发计划含 5 个均指向 `collect` 的包
- **THEN** 下一 superstep SHALL 并行执行 5 次 `collect`,SHALL NOT 去重为 1 次

#### Scenario: NODE 事件携带分支身份
- **WHEN** 上述 5 次调用执行
- **THEN** 每次调用的 NODE_START/NODE_END payload SHALL 含可区分的调用身份(如 `{"fanout_source": ..., "branch_index": i}`)

### Requirement: `_dispatch` 计划的日志推导续跑

续跑节点集的日志推导(1.3.21② 锚点规则)SHALL 扩展:当最后采纳的提交点为 `STEP_END` 且其 executed 节点中存在写入过 `_dispatch` 计划的节点时,续跑集 SHALL 取该计划的目标调用列表(含调用身份),SHALL NOT 回退到静态出边并集。planner 节点不属于最近 `STEP_END` executed 列表的陈旧计划 SHALL NOT 被采纳。推导 SHALL 纯日志派生(折叠态读取),SHALL NOT 依赖 checkpoint 缓存 metadata。

#### Scenario: 崩溃于扇出提交点之后
- **WHEN** 会话日志最后提交点为 `STEP_END`,executed 含 planner 节点,其 `_dispatch` 计划有 3 个包
- **THEN** 恢复后续跑集 SHALL 为该 3 个包的目标调用,SHALL NOT 为 planner 的静态出边

#### Scenario: 陈旧计划不采纳
- **WHEN** 最后提交点的 executed 节点不含任何 `_dispatch` 写入者,而折叠态中残留更早 superstep 的 `_dispatch` 值
- **THEN** 续跑推导 SHALL 沿用静态出边规则,SHALL NOT 采纳残留计划

### Requirement: 中断描述符兼容动态扇出

声明式中断(1.3.21①)与动态扇出叠加时:planner 节点的 `interrupt_after` SHALL 在计划已提交、分支未派发的窗口暂停;恢复后续跑集 SHALL 为派发计划的目标调用。interrupt_before 命中扇出目标节点时 SHALL 在该调用派发前暂停,描述符 `nodes` SHALL 列出目标节点。

#### Scenario: 计划评审中断
- **WHEN** planner 节点配置于 `interrupt_after` 且产出 5 包计划
- **THEN** 引擎 SHALL 在计划写入日志后、任何分支派发前暂停,恢复后按计划派发 5 个调用

#### Scenario: 目标节点上的 before 断点
- **WHEN** 扇出目标节点配置于 `interrupt_before` 且计划派发 3 个对该节点的调用
- **THEN** 引擎 SHALL 在 3 个调用执行前暂停一次,描述符 nodes SHALL 含该目标节点


### Requirement: Pregel runtime executes superstep loop
The `PregelRuntime.execute()` SHALL execute a compiled graph in superstep cycles until termination. When a FAN_OUT node is encountered, the runtime SHALL dispatch all branch nodes concurrently via `asyncio.gather` and collect results before advancing to the MERGE node.

#### Scenario: Linear execution
- **WHEN** a graph with nodes A→B→C→__end__ is executed
- **THEN** the runtime SHALL execute A, then B, then C, yielding events after each superstep

#### Scenario: Max superstep guard
- **WHEN** execution exceeds `max_supersteps` (default 100)
- **THEN** the runtime SHALL raise `RuntimeError` with message indicating possible infinite loop

#### Scenario: Fan-out parallel execution
- **WHEN** a FAN_OUT node with branches ["analyst_a", "analyst_b", "analyst_c"] is encountered
- **THEN** the runtime SHALL dispatch all 3 branch workers concurrently via `asyncio.gather`
- **AND** each branch SHALL write its result to an isolated sub-channel `_fanout__{fan_out_id}__{branch_id}`

#### Scenario: Merge aggregation after fan-out
- **WHEN** all branches of a FAN_OUT have completed and the MERGE node is the next node
- **THEN** the MERGE worker SHALL read all branch sub-channels and produce an aggregated dict output

#### Scenario: Fan-out branch failure propagates
- **WHEN** one branch of a FAN_OUT fails (raises an exception)
- **THEN** the entire fan-out execution SHALL fail and the error SHALL propagate to the caller

#### Scenario: WAL 序——append 先于 apply
- **WHEN** superstep N 的 WorkerResult 收集完成
- **THEN** 运行时 SHALL 先批量 append 事件（以 `STEP_END` 收尾）再执行通道写入

### Requirement: Interrupt/resume via checkpoint
The runtime SHALL support interrupt/resume: interrupt 时 SHALL 保存物化缓存（载荷为 `channel_state + log_version`）并 append 携带完整 `interrupt_value` payload 的 `INTERRUPT` 事件；worker 触发的 interrupt 若携带 channel writes，SHALL 先批量 append 本步的 `CHANNEL_WRITE` 事件、再以 `INTERRUPT` 事件为提交点、之后保存物化缓存——缓存 SHALL NOT 领先于日志（未达提交点的写入按撕裂尾部规则对恢复视为未发生）。resume 时 SHALL 从物化缓存 + 日志 tail 重放恢复（撕裂尾部回退到上一提交点），恢复所需的 superstep / interrupted_node / route（以及声明式中断的 phase）SHALL 由日志推导，SHALL NOT 依赖缓存 metadata。`PostgresCheckpointStore` SHALL 标记软废弃（DeprecationWarning），engine 层 `CheckpointStore` ABC SHALL 保持单键 `session_id` 契约并承载物化缝职责。该行为 SHALL 对含 FAN_OUT/MERGE 节点的图保持。

#### Scenario: Worker triggers interrupt
- **WHEN** a worker returns `Command(interrupt=value)`
- **THEN** the runtime SHALL append `INTERRUPT` 事件（完整 payload）、保存物化缓存、yield `{"type": "interrupt", "value": value}`、停止执行

#### Scenario: Worker interrupt commits writes before pausing
- **WHEN** a worker returns `Command(interrupt=value)` together with channel updates in superstep N
- **THEN** the runtime SHALL batch-append superstep N's `CHANNEL_WRITE` events（按 log policy 过滤）before the `INTERRUPT` event, and the saved cache SHALL agree with the log fold

#### Scenario: Resume from interrupt
- **WHEN** `execute()` is called with `resume_value`
- **THEN** the runtime SHALL 经缓存 + tail 重放恢复、写入 `_resume_value` 通道、从 interrupt 点之后的节点继续

#### Scenario: 恢复元数据由日志推导
- **WHEN** resume 时读取恢复上下文（interrupted_node、route）
- **THEN** 这些值 SHALL 来自日志事件（最后 `INTERRUPT` 事件 + 该节点 `CHANNEL_WRITE` delta），而非缓存 metadata

#### Scenario: Projection equivalence holds across interrupt pause
- **WHEN** a session is resumed after a worker-authored or declarative interrupt
- **THEN** the restored channel state SHALL agree with a full-log fold (projection-equivalent), SHALL NOT diverge on the interrupting superstep's writes

#### Scenario: Engine layer has no PostgresCheckpointStore
- **WHEN** examining `runtime/checkpoint.py`
- **THEN** it SHALL contain only `CheckpointStore` ABC and `InMemoryCheckpointStore`
- **AND** it SHALL NOT import from `models/`, `services/`, or `sqlalchemy`

#### Scenario: PostgresCheckpointStore 软废弃
- **WHEN** production code instantiates `PostgresCheckpointStore`
- **THEN** SHALL 发出 DeprecationWarning 并指向迁移文档

### Requirement: Multi-key edge resolution
The `_resolve_next_nodes` method SHALL support multi-key conditional routing by looking up the `_route` value in the edge target dict, with fallback to "default" and then "false".

#### Scenario: Multi-key routing
- **WHEN** `_route` is "finance" and edge target is `{"finance": "fin_agent", "tech": "tech_agent", "default": "general_agent"}`
- **THEN** execution SHALL route to "fin_agent"

#### Scenario: Default fallback
- **WHEN** `_route` is "unknown" and edge target has "default" key
- **THEN** execution SHALL route to the "default" target

#### Scenario: Legacy false fallback
- **WHEN** `_route` is "unknown" and edge target has no "default" but has "false" key
- **THEN** execution SHALL route to the "false" target

### Requirement: checkpoint 降级为物化缓存（节奏与载荷）

`CheckpointStore.save` 载荷 SHALL 瘦身为 `channel_state + log_version`；死字段 `pending_writes` SHALL 从签名移除。保存节奏 SHALL 为：turn 正常结束 / interrupt / 每 N superstep（默认 10，配置化）。InMemoryCheckpointStore SHALL 继续服务测试。

#### Scenario: 载荷瘦身
- **WHEN** 物化缓存被保存
- **THEN** 记录 SHALL 仅含 `channel_state` 与 `log_version`，无 `pending_writes`

#### Scenario: 节奏为每 N 步
- **WHEN** 一次执行连续运行 25 个 superstep（无 interrupt）
- **THEN** 物化保存发生约 2 次（第 10、20 步）加 turn 结束 1 次

### Requirement: 运行时不变式断言 + fail-stop（snapshot validation）

恢复 / interrupt / 物化时 SHALL 校验日志投影与通道快照等价（非 LogPolicy 排除通道）；不等价时 SHALL 抛 invariant error 并中止（fail-stop），SHALL NOT 热修复运行中状态。

#### Scenario: 断言失败中止执行
- **WHEN** 恢复时投影与快照不一致
- **THEN** SHALL 抛 invariant error，本次执行中止，日志保持完整

### Requirement: CheckpointStore 物化缝的租户无感知

引擎层 `CheckpointStore` SHALL 保持 `session_id` 单键契约；生产物化 SHALL 经 services 层 adapter（SessionStateMaterializer）以 `tenant_context_provider` 闭包模式写入 SessionStateStore（复用 `PostgresEventStore` 的租户注入先例）。

#### Scenario: 引擎不感知租户
- **WHEN** PregelRuntime 触发物化保存
- **THEN** 引擎仅传递 `session_id`；`(org_id, user_id)` 由 adapter 层的 context provider 注入

### Requirement: Declarative interrupts at superstep boundaries
The runtime SHALL support compile-time declarative interrupts via `interrupt_before` / `interrupt_after` node lists carried on the compiled graph. `interrupt_before` SHALL pause the entire superstep before any node is dispatched when any scheduled node of that superstep is in the list; `interrupt_after` SHALL pause after every result of the superstep has been WAL-appended and applied when any executed node is in the list. On pause the runtime SHALL append an `INTERRUPT` event whose payload is a declarative descriptor (`kind="declarative"`, `phase` ∈ {"before","after"}, the pause-relevant `nodes` set, a unique `interrupt_id`, `superstep`, `remaining_steps`), save the materialized cache, yield `{"type": "interrupt", "value": <descriptor>}`, emit the paired `TURN_END` with reason "interrupt", and stop. Resume SHALL derive `phase` and the resume node set from the last unclosed `INTERRUPT` event in the log (log-as-truth; SHALL NOT depend on cache metadata): `phase="before"` → the paused superstep's scheduled nodes execute themselves; `phase="after"` → execution continues from the union of out-edges of all nodes executed in the interrupting superstep. `resume_value` SHALL be injected into the `_resume_value` channel as today. 该行为 SHALL 对含 FAN_OUT/MERGE 节点的图保持;task mode 由编译期拒绝(见 graph-dsl),运行时不重复校验。

#### Scenario: interrupt_before pauses before dispatch
- **WHEN** a graph declares `interrupt_before: ["review_gate"]` and `review_gate` is scheduled for superstep N
- **THEN** the runtime SHALL pause before any node of superstep N executes, append an `INTERRUPT` event with `phase="before"`, save the materialized cache, and yield `{"type": "interrupt", ...}`

#### Scenario: interrupt_after pauses after writes are committed
- **WHEN** a graph declares `interrupt_after: ["planner"]` and `planner` completes in superstep N
- **THEN** all channel writes of superstep N SHALL be WAL-appended and applied before the pause, the `INTERRUPT` event SHALL carry `phase="after"`, and no further node SHALL execute

#### Scenario: Whole superstep pauses when one node matches
- **WHEN** superstep N schedules nodes X and Y concurrently and only X is in `interrupt_before`
- **THEN** neither X nor Y SHALL execute in superstep N, and on resume both X and Y SHALL execute

#### Scenario: Resume after a before-interrupt executes the paused nodes
- **WHEN** execution resumes with `resume_value` after a `phase="before"` pause at node X
- **THEN** node X itself SHALL execute, and the `_resume_value` channel SHALL hold the human-provided value

#### Scenario: Resume after an after-interrupt follows all executed out-edges
- **WHEN** execution resumes after a `phase="after"` pause in a superstep that executed nodes X and Y
- **THEN** the next node set SHALL be the union of out-edges of X and Y, with conditional edges resolved via the persisted `_route` writes

#### Scenario: Entry node before-interrupt anchors initial state
- **WHEN** a graph declares `interrupt_before` containing the entry node
- **THEN** the first superstep SHALL pause before the entry node executes, and the materialized cache SHALL capture the initial input state

#### Scenario: Declarative interrupt is log-indistinguishable to resume gate
- **WHEN** a session's event log contains an unclosed declarative `INTERRUPT` event
- **THEN** the log-derived resume acceptance logic SHALL treat it identically to a worker-authored interrupt (same `EventType.INTERRUPT`)

#### Scenario: TURN_END pairing maintained on declarative pause
- **WHEN** a declarative pause occurs mid-turn
- **THEN** the runtime SHALL emit `TURN_END` with reason "interrupt" so the T0.5 audit pair stays closed

### Requirement: remaining_steps exposed in execution context
The runtime SHALL expose `remaining_steps`（= `max_supersteps` − 当前 superstep）in the per-superstep worker execution context, so workers can degrade gracefully before `MaxSuperstepsError`. After a checkpoint restore the value SHALL reflect the restored superstep counter（人类等待时间不消耗步数预算）。

#### Scenario: Worker reads remaining_steps
- **WHEN** a worker executes in superstep N of a run with `max_supersteps=100`
- **THEN** its execution context SHALL contain `remaining_steps = 100 - N`

#### Scenario: remaining_steps reflects restored counter after resume
- **WHEN** execution resumes from a checkpoint saved at superstep N
- **THEN** the first post-resume superstep's execution context SHALL compute `remaining_steps` from the restored counter, not from zero

### Requirement: 续跑节点集的日志推导

恢复路径 SHALL 从会话日志的**最后一个提交点描述符**推导续跑节点集,规则按提交点类别:

1. `INTERRUPT`(未闭合):沿用既有 phase-aware 推导——`before` 执行被暂停节点本身,`after` 求出边并集,worker-authored 求中断节点出边(① 语义逐字保持);
2. `FORK`:取 payload 的 `next_nodes`;
3. `STEP_END`(尾部崩溃恢复):取自上一提交点以来的 `NODE_END` 节点集,求出边并集,条件边经恢复后的 `_route` 通道裁决;
4. 无任何提交点(空日志):取图入口。

携带 `source="update_state"` 的 `STEP_END`(状态修改批次收尾)SHALL NOT 作为续跑锚点——修改不是执行进度,推导时跳过并向前找上一个提交点。推导 SHALL NOT 依赖 checkpoint 缓存 metadata(缓存仅作折叠加速);多来源混存时按日志顺序取最后者。

#### Scenario: FORK 描述符续跑
- **WHEN** 子会话日志最后提交点为 `FORK`,payload `next_nodes=["plan_review"]`
- **THEN** 恢复后首个 superstep SHALL 调度 `plan_review` 而非图入口

#### Scenario: 尾部崩溃恢复推导
- **WHEN** 会话日志最后提交点为 `STEP_END`,其前一个提交点之后存在 `NODE_END(A)`、`NODE_END(B)`
- **THEN** 恢复 SHALL 从 A 与 B 的出边并集(条件边按恢复的 `_route` 值)调度

#### Scenario: 描述符优先级按日志顺序
- **WHEN** 日志中 `FORK` 之后又出现未闭合 `INTERRUPT`
- **THEN** 续跑节点集 SHALL 按 `INTERRUPT` 的 phase-aware 规则推导

### Requirement: resume_from 仅限日志尾部

引擎执行入口接受 `resume_from`(日志版本)参数时,SHALL 仅在该版本等于会话当前日志尾部版本时执行恢复(崩溃恢复语义);版本小于尾部时 SHALL 抛出错误并明示"历史点恢复必须经 fork 创建子会话"。历史点 fork 的编排属服务层职责,引擎 SHALL NOT 在同一会话日志内产生双时间线。

#### Scenario: 尾部版本放行
- **WHEN** `resume_from` 等于当前日志尾部版本
- **THEN** 引擎 SHALL 折叠至该版本并按续跑推导继续执行

#### Scenario: 历史版本拒绝
- **WHEN** `resume_from` 小于当前日志尾部版本
- **THEN** 引擎 SHALL 抛出带指引信息的错误,且 SHALL NOT 向日志追加任何事件
