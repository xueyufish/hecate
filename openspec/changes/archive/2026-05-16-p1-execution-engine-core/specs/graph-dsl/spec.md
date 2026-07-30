## ADDED Requirements — 新增需求

### Requirement: Graph JSON Schema 定义 — Graph JSON Schema Definition

Graph DSL SHALL use JSON format with 5 top-level fields: `version` (MUST be `"1.0"`), `name`, `state` (Channel declarations with `type`), `nodes` (Map, key = NodeId), `edges` (Array).

#### Scenario: 合法 Graph JSON 通过校验 — Valid Graph JSON passes validation
- **WHEN** 提交包含完整五个顶层字段的 Graph JSON
- **THEN** 编译器接受并输出 CompiledGraph

#### Scenario: 缺少必需顶层字段被拒绝 — Missing required field rejected
- **WHEN** 提交缺少 `nodes` 字段的 Graph JSON
- **THEN** 编译器 MUST 抛出 `GraphValidationError`

### Requirement: 四种节点类型 — Four Node Types

DSL SHALL support `conversation`, `tool-call`, `condition`, `agent` node types. Each MUST include `type` and `config`.

#### Scenario: conversation 节点配置合法 — Valid conversation node config
- **WHEN** 定义 type 为 `conversation` 且 config 包含 `model` 和 `system_prompt`
- **THEN** 编译器接受该节点

#### Scenario: 未知节点类型被拒绝 — Unknown node type rejected
- **WHEN** 定义 type 为 `"unknown_type"`
- **THEN** 编译器 MUST 抛出 `GraphValidationError`

### Requirement: 边协议与 Command — Edge Protocol and Command

Edges SHALL support simple (single target) and conditional (targets Map). Nodes control flow via `Command`: `Command(goto=node_id)`, `Command(return=value)`, `Command(interrupt=value)`.

#### Scenario: 条件边正确路由 — Conditional edge routes correctly
- **WHEN** condition 节点返回路由键，条件边 targets 包含对应映射
- **THEN** 引擎 MUST 路由到目标节点

#### Scenario: Command(goto) 跳转执行 — Command(goto) jump
- **WHEN** 节点返回 `Command(goto="other_node", update={"key": "value"})`
- **THEN** 引擎 SHALL 跳转并写入 update

### Requirement: Graph 编译器 — Graph Compiler

编译器 SHALL convert JSON DSL to `CompiledGraph`. Process: Schema validation → edge connectivity check → Channel binding analysis → unreachable node detection.

#### Scenario: 检测到悬空边引用 — Detecting dangling edge references
- **WHEN** 边的 target 引用不存在的节点
- **THEN** 编译器 MUST 抛出 `GraphValidationError`

#### Scenario: 检测到不可达节点 — Detecting unreachable nodes
- **WHEN** 存在节点没有任何边指向它（非 entry_point）
- **THEN** 编译器 MUST 产生编译警告，但 SHALL NOT 阻止编译

### Requirement: Channel 类型系统 — Channel Type System

Channel types: `last_value` (newest value), `topic` (append messages), `persistent_topic` (append + persistent), `accumulator` (aggregation).

#### Scenario: last_value Channel 只保留最新值 — last_value retains only newest
- **WHEN** 连续写入值 `"a"`、`"b"`、`"c"`
- **THEN** 读取返回 `"c"`

#### Scenario: topic Channel 追加所有消息 — topic appends all messages
- **WHEN** 连续写入三条消息
- **THEN** 读取返回包含三条消息的列表

#### Scenario: accumulator Channel 聚合值 — accumulator aggregates values
- **WHEN** initial=0, reduce="add", 依次写入 1、2、3
- **THEN** 读取返回 6

### Requirement: 序列化与反序列化 — Serialization and Deserialization

DSL SHALL support JSON serialization/deserialization. `CompiledGraph` MUST provide `to_json()`. `__start__` and `__end__` are reserved identifiers.

#### Scenario: __start__ 保留字冲突 — Reserved word conflict
- **WHEN** 用户在 nodes 中定义 key 为 `"__start__"`
- **THEN** 编译器 MUST 抛出 `GraphValidationError`
