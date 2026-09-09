## ADDED Requirements

### Requirement: 动态扇出——运行时派发包

路由节点(CUSTOM 略——v1 限 CONDITION 类型)SHALL 支持在运行时产出 N 个派发包(dispatch packet),每包形如 `{"node": <目标节点ID>, "state": <分支载荷>}`,写入 `_dispatch` 通道。N 由运行时数据决定(如 over 通道的元素个数),SHALL NOT 受编译期静态配置限制。静态 FAN_OUT 分支表行为保持不变,既有图零迁移。

#### Scenario: map-over-channel 产出 N 个派发包
- **WHEN** CONDITION 节点配置 `{"fanout": {"over": "queries", "target": "collect", "state_key": "query"}}` 且 `queries` 通道含 5 个元素
- **THEN** 该节点执行 SHALL 产出 5 个派发包写入 `_dispatch`,每包目标为 `collect`,载荷为 `{"query": <对应元素>}`

#### Scenario: 空 over 通道零派发
- **WHEN** `fanout` 配置的 over 通道为空列表
- **THEN** `_dispatch` SHALL 为空列表,该节点 SHALL NOT 派发任何分支

### Requirement: 派发计划优先于静态边解析

某节点执行结果携带 `_dispatch` 计划(非 None)时,该节点的静态出边解析 SHALL 被派发计划取代(优先级同 `Command(goto)`);下一 superstep SHALL 按计划派发调用列表。计划为空列表时 SHALL 零派发(仅当同 superstep 其他结果产生后续节点时执行才继续)。

#### Scenario: 派发计划取代出边
- **WHEN** CONDITION 节点写入 `_dispatch` 且图配置中该节点存在指向 `__end__` 的出边
- **THEN** 下一 superstep SHALL 按派发计划调度,SHALL NOT 因出边指向 `__end__` 而终止

#### Scenario: 无派发计划时行为不变
- **WHEN** 普通节点的结果不含 `_dispatch`
- **THEN** 出边解析 SHALL 沿用既有 `_route` 语义

### Requirement: 分支状态 seed 语义

每个派发调用的输入 SHALL 为「派发时全量 snapshot + 本包载荷写入本调用专属子通道」;分支 worker SHALL 能从其子通道读取自己的状态切片,SHALL NOT 观察到同批其他分支的子通道写入(superstep 提交前互相隔离)。子通道命名 SHALL 由源节点与调用身份派生且全局唯一;map 场景对同一目标节点的 N 次调用 SHALL 各自拥有独立子通道。

#### Scenario: 分支读取自己的切片
- **WHEN** 派发包载荷为 `{"query": "q3"}` 且该调用子通道为 `S`
- **THEN** 该分支 worker 执行时 SHALL 能从 `S` 读到 `{"query": "q3"}`

#### Scenario: 同目标多调用互相隔离
- **WHEN** 5 个派发调用共享目标节点 `collect`
- **THEN** 5 个调用 SHALL 各自拥有独立子通道,任一分支 SHALL NOT 读到其他分支的切片

### Requirement: per-branch 容错策略

分支失败处理 SHALL 可配置(`on_branch_error`):默认 `fail_fast`(任一分支失败即整批失败,保持既有静态扇出行为);`collect` 模式下失败分支 SHALL 记录错误条目(目标子通道携带错误信息),其余分支照常完成,执行 SHALL NOT 因单个分支失败中断。

#### Scenario: fail_fast 默认保持
- **WHEN** 分支 B 抛出异常且未配置 `on_branch_error`
- **THEN** 整个扇出 SHALL 以该错误失败,与既有静态 FAN_OUT 行为一致

#### Scenario: collect 模式记录失败分支
- **WHEN** `on_branch_error: "collect"` 且分支 B 失败、分支 A/C 成功
- **THEN** B 的子通道 SHALL 携带错误条目,A/C SHALL 正常完成,MERGE 聚合结果中 SHALL 能区分 B 的失败条目

### Requirement: 分层扇出上限

引擎 SHALL 强制分层扇出上限:节点级单次派发包数上限与 superstep 级调用总数上限(引擎默认值 + 图/节点级覆盖)。超限 SHALL 以显式错误失败(fail closed),SHALL NOT 静默截断。

#### Scenario: 超节点级上限失败
- **WHEN** 一次派发产出 200 个包且节点级上限为 64
- **THEN** 引擎 SHALL 抛出带上限信息的错误,且 SHALL NOT 派发任何分支

#### Scenario: 图内配置覆盖引擎默认
- **WHEN** 图配置显式声明更高(或更低)的扇出上限且未超平台配额边界
- **THEN** 引擎 SHALL 按图配置的上限执行

### Requirement: 动态 MERGE 计划发现

MERGE 节点 SHALL 支持聚合动态扇出的产出:当 `fan_out_source` 指向动态扇出源时,MERGE SHALL 从折叠态中的派发计划(而非编译期 `branches` 配置)发现分支集合,读取全部对应子通道,并按计划中的调用身份输出聚合结果;聚合结果写入 ACCUMULATOR 通道时 SHALL 经该通道注册的 reducer 合并。静态路径的 `{branch_id: result}` 聚合行为保持不变。

#### Scenario: 动态源聚合
- **WHEN** MERGE 的 `fan_out_source` 为动态扇出源,计划含 5 个对 `collect` 的调用,4 成功 1 失败(collect 模式)
- **THEN** MERGE SHALL 按 5 个调用身份输出聚合,失败调用 SHALL 携带错误条目

#### Scenario: 静态路径不变
- **WHEN** MERGE 的 `fan_out_source` 指向静态 FAN_OUT 节点
- **THEN** 聚合行为 SHALL 与既有 `{branch_id: result}` 语义逐字一致

### Requirement: 与 COORDINATOR 的边界

动态扇出 SHALL 限定为单 superstep 内、父会话通道空间内的图级并行:分支调用出现在父会话日志中,共享父会话通道。跨 superstep、隔离子会话、自带 LLM 规划器的编排 SHALL 仍属 COORDINATOR(1.3.18)职责,SHALL NOT 因本能力而重复建设。

#### Scenario: 分支在父会话日志可见
- **WHEN** 动态扇出派发 3 个分支
- **THEN** 3 个分支的 NODE_START/NODE_END 事件 SHALL 出现在父会话日志中,SHALL NOT 创建子会话
