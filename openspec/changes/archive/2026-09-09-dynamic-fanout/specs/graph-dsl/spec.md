## ADDED Requirements

### Requirement: map-over-channel 声明式扇出配置

Graph DSL SHALL 支持在 CONDITION 节点配置 `fanout` 声明动态扇出:`{"fanout": {"over": <通道名>, "target": <目标节点ID>, "state_key": <载荷键名>, "max_fanout": <可选上限>}}`。执行时对该通道当前值的每个元素产出一个派发包(目标为 `target`,载荷为 `{state_key: 元素}`)。v1 SHALL NOT 提供 LLM 规划器模式或 worker 自主派发(`Command(goto=[...])`)——两者显式留待后续(5.11 / 独立变更)。

#### Scenario: fanout 配置的节点产出计划
- **WHEN** 图 DSL 含 `{"type": "condition", "config": {"fanout": {"over": "queries", "target": "collect", "state_key": "query"}}}`
- **THEN** 该节点执行时 SHALL 按 `queries` 的元素个数产出派发包,无需任何 Python 边函数

#### Scenario: 未配置 fanout 的 CONDITION 节点行为不变
- **WHEN** CONDITION 节点无 `fanout` 配置
- **THEN** 其执行与 `_route` 边解析 SHALL 与既有语义逐字一致

### Requirement: 扇出配置编译期校验

编译器 SHALL 对 `fanout` 配置做编译期校验:`over` 通道必须存在于图 state;`target` 必须是图内存在的节点;`state_key` 必须非空;`max_fanout`(若提供)必须为正整数。校验失败 SHALL 报 `GraphValidationError` 并指明字段。`fanout` 配置于非 CONDITION 类型节点时 SHALL 被拒绝。

#### Scenario: 目标节点不存在
- **WHEN** `fanout.target` 指向图中不存在的节点
- **THEN** 编译 SHALL 失败,错误信息 SHALL 指明 `fanout.target` 字段与缺失的节点 ID

#### Scenario: over 通道未声明
- **WHEN** `fanout.over` 引用未在图 state 声明的通道
- **THEN** 编译 SHALL 失败,错误信息 SHALL 指明 `fanout.over` 字段
