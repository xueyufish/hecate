## MODIFIED Requirements — 修改的需求

### Requirement: Pregel 运行时执行 superstep 循环 — Pregel 运行时执行 superstep 循环
`PregelRuntime.execute()` SHALL 以 superstep 循环方式执行编译后的图，直到终止。当遇到 FAN_OUT 节点时，运行时 SHALL 通过 `asyncio.gather` 并发分发所有分支节点，并在进入 MERGE 节点之前收集结果。

#### Scenario: 线性执行
- **WHEN** 执行一个包含节点 A→B→C→__end__ 的图
- **THEN** 运行时 SHALL 依次执行 A、B、C，在每个 superstep 后生成事件

#### Scenario: 最大 superstep 保护
- **WHEN** 执行超过 `max_supersteps`（默认 100）
- **THEN** 运行时 SHALL 引发 `RuntimeError`，消息指示可能的无限循环

#### Scenario: 扇出并行执行
- **WHEN** 遇到一个带有分支 ["analyst_a", "analyst_b", "analyst_c"] 的 FAN_OUT 节点
- **THEN** 运行时 SHALL 通过 `asyncio.gather` 并发分发所有 3 个分支 worker
- **AND** 每个分支 SHALL 将其结果写入隔离的子通道 `_fanout__{fan_out_id}__{branch_id}`

#### Scenario: 扇出后的合并聚合
- **WHEN** FAN_OUT 的所有分支都已完成且 MERGE 节点是下一个节点
- **THEN** MERGE worker SHALL 读取所有分支子通道并生成聚合的字典输出

#### Scenario: 扇出分支失败传播
- **WHEN** FAN_OUT 的一个分支失败（引发异常）
- **THEN** 整个扇出执行 SHALL 失败，错误 SHALL 传播给调用者

### Requirement: 通过 checkpoint 中断/恢复 — 通过 checkpoint 中断/恢复
运行时 SHALL 支持通过将完整状态持久化到 checkpoint 来中断/恢复，并在恢复时恢复。此行为 SHALL 对包含 FAN_OUT/MERGE 节点的图保留。

#### Scenario: Worker 触发中断
- **WHEN** worker 返回 `Command(interrupt=value)`
- **THEN** 运行时 SHALL 保存带有中断元数据的 checkpoint，生成 `{"type": "interrupt", "value": value}`，并停止执行

#### Scenario: 从中断恢复
- **WHEN** 使用 `resume_value` 调用 `execute()`
- **THEN** 运行时 SHALL 从最后一个 checkpoint 恢复，将 `resume_value` 写入 `_resume_value` 通道，并从中断点之后的节点继续执行

### Requirement: 多键边解析 — 多键边解析
`_resolve_next_nodes` 方法 SHALL 通过在边目标字典中查找 `_route` 值来支持多键条件路由，并回退到 "default" 然后是 "false"。

#### Scenario: 多键路由
- **WHEN** `_route` 为 "finance" 且边目标为 `{"finance": "fin_agent", "tech": "tech_agent", "default": "general_agent"}`
- **THEN** 执行 SHALL 路由到 "fin_agent"

#### Scenario: 默认回退
- **WHEN** `_route` 为 "unknown" 且边目标具有 "default" 键
- **THEN** 执行 SHALL 路由到 "default" 目标

#### Scenario: 旧版 false 回退
- **WHEN** `_route` 为 "unknown" 且边目标没有 "default" 但有 "false" 键
- **THEN** 执行 SHALL 路由到 "false" 目标