## MODIFIED Requirements — 修改的需求

### Requirement: 编译器验证入口点、边和交接循环 — 编译器验证入口点、边和交接循环
`GraphCompiler.compile()` SHALL 在生成 `CompiledGraph` 之前执行四个验证阶段：入口点、边、交接循环以及扇出/合并结构约束。

#### Scenario: 未找到入口点
- **WHEN** 声明的入口点引用了一个不存在的节点
- **THEN** 它 SHALL 引发 `GraphValidationError`，field="entry"

#### Scenario: 边目标引用不存在的节点
- **WHEN** 边目标既不是声明的节点 ID 也不是哨兵（`__start__`、`__end__`）
- **THEN** 它 SHALL 引发 `GraphValidationError`，字段指示边路径

#### Scenario: 不可达节点记录为警告
- **WHEN** 存在从入口点通过 BFS 不可达的节点
- **THEN** 编译器 SHALL 记录包含不可达节点 ID 的 WARNING，但 SHALL NOT 引发错误

#### Scenario: 非代理节点之间的交接
- **WHEN** 交接边的源或目标不是 AGENT 类型的节点
- **THEN** 它 SHALL 引发 `GraphValidationError`

#### Scenario: 有扇出无合并
- **WHEN** 图包含 FAN_OUT 节点但从其任何分支都无法到达 MERGE 节点
- **THEN** 它 SHALL 引发 `GraphValidationError`，消息为 "FAN_OUT node '{id}' has no reachable MERGE node"

#### Scenario: 有合并无扇出
- **WHEN** 图包含 MERGE 节点但上游没有 FAN_OUT 节点
- **THEN** 它 SHALL 引发 `GraphValidationError`，消息为 "MERGE node '{id}' has no upstream FAN_OUT node"

#### Scenario: 扇出分支必须匹配合并
- **WHEN** FAN_OUT 节点有 3 个分支但下游 MERGE 节点的配置列出了不同的 fan_out_source
- **THEN** 它 SHALL 引发 `GraphValidationError`

### Requirement: Graph DSL 解析器根据 JSON Schema 验证 — Graph DSL 解析器根据 JSON Schema 验证
`parse_graph()` 函数 SHALL 接受 JSON 字符串或字典，并根据 `schemas/graph-dsl.schema.json` 进行验证。schema SHALL 包括 "fan-out" 和 "merge" 作为有效的节点类型枚举值。

#### Scenario: 有效的 JSON 图
- **WHEN** 使用有效输入调用 `parse_graph('{"version":"1.0","nodes":{...},"edges":[...]}')`
- **THEN** 它 SHALL 返回一个包含类型化 `ChannelDef`、`NodeConfig` 和 `Edge` 对象的 `GraphConfig`

#### Scenario: JSON 中的扇出节点
- **WHEN** `parse_graph(...)` 遇到一个类型为 `"type": "fan-out"` 的节点
- **THEN** 它 SHALL 创建一个 `NodeType.FAN_OUT` 的 `NodeConfig`

#### Scenario: JSON 中的合并节点
- **WHEN** `parse_graph(...)` 遇到一个类型为 `"type": "merge"` 的节点
- **THEN** 它 SHALL 创建一个 `NodeType.MERGE` 的 `NodeConfig`