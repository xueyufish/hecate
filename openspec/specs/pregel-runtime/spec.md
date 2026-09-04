## Purpose

The Pregel runtime executes compiled graphs as **superstep cycles**: each round reads channels, dispatches scheduled nodes concurrently, applies writes, persists checkpoint state, and yields stream events until termination or interrupt. It is the engine layer that orchestrates graph execution and integrates with the event log (log-as-truth in 1.3.19).

## Requirements

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
The runtime SHALL support interrupt/resume: interrupt 时 SHALL 保存物化缓存（载荷为 `channel_state + log_version`）并 append 携带完整 `interrupt_value` payload 的 `INTERRUPT` 事件；resume 时 SHALL 从物化缓存 + 日志 tail 重放恢复（撕裂尾部回退到上一提交点），恢复所需的 superstep / interrupted_node / route SHALL 由日志推导，SHALL NOT 依赖缓存 metadata。`PostgresCheckpointStore` SHALL 标记软废弃（DeprecationWarning），engine 层 `CheckpointStore` ABC SHALL 保持单键 `session_id` 契约并承载物化缝职责。该行为 SHALL 对含 FAN_OUT/MERGE 节点的图保持。

#### Scenario: Worker triggers interrupt
- **WHEN** a worker returns `Command(interrupt=value)`
- **THEN** the runtime SHALL append `INTERRUPT` 事件（完整 payload）、保存物化缓存、yield `{"type": "interrupt", "value": value}`、停止执行

#### Scenario: Resume from interrupt
- **WHEN** `execute()` is called with `resume_value`
- **THEN** the runtime SHALL 经缓存 + tail 重放恢复、写入 `_resume_value` 通道、从 interrupt 点之后的节点继续

#### Scenario: 恢复元数据由日志推导
- **WHEN** resume 时读取恢复上下文（interrupted_node、route）
- **THEN** 这些值 SHALL 来自日志事件（最后 `INTERRUPT` 事件 + 该节点 `CHANNEL_WRITE` delta），而非缓存 metadata

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
