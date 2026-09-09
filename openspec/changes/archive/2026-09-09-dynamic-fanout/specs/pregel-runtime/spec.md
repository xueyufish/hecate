## ADDED Requirements

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
