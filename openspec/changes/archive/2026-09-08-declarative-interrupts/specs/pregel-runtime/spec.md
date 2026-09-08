## ADDED Requirements

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

## MODIFIED Requirements

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
