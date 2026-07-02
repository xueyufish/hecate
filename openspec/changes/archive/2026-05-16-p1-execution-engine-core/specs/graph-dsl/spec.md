## ADDED Requirements

### Requirement: Graph JSON Schema 定义

Graph DSL SHALL 使用 JSON 格式定义工作流，包含 `version`、`name`、`state`、`nodes`、`edges` 五个顶层字段。`version` MUST 为 `"1.0"`。`state` 定义 Channel 声明，每个 Channel MUST 包含 `type` 字段（取值 `last_value` | `topic` | `persistent_topic` | `accumulator`）。`nodes` MUST 是 Map 结构，key 为 NodeId。`edges` MUST 为数组。

#### Scenario: 合法 Graph JSON 通过校验
- **WHEN** 提交包含完整五个顶层字段且所有字段类型合法的 Graph JSON
- **THEN** 编译器接受该 JSON 并输出 CompiledGraph，不抛出任何错误

#### Scenario: 缺少必需顶层字段被拒绝
- **WHEN** 提交缺少 `nodes` 字段的 Graph JSON
- **THEN** 编译器 MUST 抛出 `GraphValidationError`，错误信息包含缺少的字段名

### Requirement: 四种节点类型

Graph DSL SHALL 支持 `conversation`、`tool-call`、`condition`、`agent` 四种节点类型。每个节点 MUST 包含 `type` 字段和 `config` 对象。`conversation` 节点的 config MUST 包含 `model` 和 `system_prompt`。`tool-call` 节点的 config MUST 包含 `tool_name`。`condition` 节点的 config MUST 包含 `expression`。`agent` 节点的 config MUST 包含 `agent_ref` 或 `skill_ref`。

#### Scenario: conversation 节点配置合法
- **WHEN** 定义 type 为 `conversation` 的节点且 config 包含 `model` 和 `system_prompt`
- **THEN** 编译器接受该节点并正确绑定 LLM 调用配置

#### Scenario: 未知节点类型被拒绝
- **WHEN** 定义 type 为 `"unknown_type"` 的节点
- **THEN** 编译器 MUST 抛出 `GraphValidationError`，错误信息提示不支持的节点类型

### Requirement: 边协议与 Command

Edge SHALL 支持两种形式：简单边（source → target 单一目标）和条件边（source → targets Map）。条件边的 targets MUST 是 string key 到 NodeId 的映射。节点可通过 Command 控制执行流：`Command(goto=node_id)` 跳转到指定节点，`Command(return=value)` 返回结果并结束当前图执行，`Command(interrupt=value)` 暂停执行等待外部输入。

#### Scenario: 条件边正确路由
- **WHEN** condition 节点执行后返回路由结果 `"true"`，且条件边 targets 包含 `{"true": "plan", "false": "__end__"}`
- **THEN** 执行引擎 MUST 将执行流路由到 `"plan"` 节点

#### Scenario: Command(goto) 跳转执行
- **WHEN** 节点返回 `Command(goto="other_node", update={"key": "value"})`
- **THEN** 执行引擎 SHALL 跳转到 `other_node` 节点，并将 update 中的值写入对应 Channel

#### Scenario: Command(interrupt) 暂停执行
- **WHEN** 节点返回 `Command(interrupt={"type": "approval", "message": "请确认"})`
- **THEN** 执行引擎 MUST 暂停 Pregel 循环，保存 Checkpoint，并返回 interrupt 信息给调用方

### Requirement: Graph 编译器

Graph 编译器 SHALL 将 JSON DSL 转换为 `CompiledGraph` 对象。编译过程 MUST 依次执行：Schema 校验 → 边连接合法性检查（所有 source/target 节点必须存在）→ Channel 读写绑定分析 → 不可达节点检测。编译成功后输出 `CompiledGraph`，包含 `nodes`、`edges`、`channels`、`entry_point` 四个属性。

#### Scenario: 编译简单线性图
- **WHEN** 提交一个三节点线性图 `__start__ → A → B → __end__`
- **THEN** 编译器输出 CompiledGraph，entry_point 为 `"A"`，edges 包含两条边

#### Scenario: 检测到悬空边引用
- **WHEN** 边的 target 引用了一个不存在的节点 `"nonexistent"`
- **THEN** 编译器 MUST 抛出 `GraphValidationError`，提示引用的节点不存在

#### Scenario: 检测到不可达节点
- **WHEN** 图中存在节点 `"orphan"` 且没有任何边指向它（非 entry_point）
- **THEN** 编译器 MUST 产生编译警告，但 SHALL NOT 阻止编译

### Requirement: Channel 类型系统

Channel 类型系统 SHALL 支持 `last_value`（新值覆盖旧值）、`topic`（追加消息，支持 reducer）、`persistent_topic`（追加 + 持久化，不可删除）、`accumulator`（按指定函数聚合）四种类型。`accumulator` MUST 在 `state` 定义中包含 `initial` 初始值和 `reduce` 聚合函数名。`topic` MUST 支持 `append` reducer。

#### Scenario: last_value Channel 只保留最新值
- **WHEN** 对 `last_value` 类型的 Channel 连续写入值 `"a"`、`"b"`、`"c"`
- **THEN** 读取该 Channel MUST 返回 `"c"`

#### Scenario: topic Channel 追加所有消息
- **WHEN** 对 `topic` 类型的 Channel 连续写入三条消息
- **THEN** 读取该 Channel MUST 返回包含三条消息的列表

#### Scenario: accumulator Channel 聚合值
- **WHEN** 定义 accumulator Channel（initial=0, reduce="add"），依次写入值 1、2、3
- **THEN** 读取该 Channel MUST 返回 6

### Requirement: 序列化与反序列化

Graph DSL SHALL 支持完整的 JSON 序列化和反序列化。`CompiledGraph` MUST 提供 `to_json()` 方法输出合法的 Graph JSON。反序列化 MUST 能从合法 JSON 还原出语义等价的 CompiledGraph。特殊节点标识 `__start__` 和 `__end__` MUST 为保留字，不允许用户定义同名节点。

#### Scenario: 编译后序列化再反序列化结果一致
- **WHEN** 将一个合法 Graph JSON 编译为 CompiledGraph，再调用 to_json() 序列化
- **THEN** 序列化后的 JSON 反序列化再编译，SHALL 产生语义等价的 CompiledGraph

#### Scenario: __start__ 保留字冲突
- **WHEN** 用户在 nodes 中定义 key 为 `"__start__"` 的节点
- **THEN** 编译器 MUST 抛出 `GraphValidationError`，提示保留字冲突
