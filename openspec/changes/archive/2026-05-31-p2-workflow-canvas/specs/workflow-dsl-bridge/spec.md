## ADDED Requirements — 新增需求

### Requirement: Convert React Flow state to Graph DSL — 将 React Flow 状态转换为 Graph DSL
The system SHALL provide a function `flowToDsl(nodes: Node[], edges: Edge[]) → GraphDsl` that converts the visual canvas state into a valid Graph DSL JSON object conforming to `graph-dsl.schema.json`.

系统应提供一个函数 `flowToDsl(nodes: Node[], edges: Edge[]) → GraphDsl`，将可视化画布状态转换为符合 `graph-dsl.schema.json` 的有效 Graph DSL JSON 对象。

#### Scenario: Convert simple two-node flow — 转换简单的两节点流程
- **WHEN** canvas has a start node connected to a conversation node via one edge
- **THEN** the output DSL contains `{"version": "1.0", "name": "...", "nodes": {"node_1": {"type": "conversation", "config": {...}}}, "edges": [{"source": "__start__", "target": "node_1"}]}`
- **当**画布上有一个开始节点通过一条边连接到 conversation 节点
- **则**输出的 DSL 包含 `{"version": "1.0", "name": "...", "nodes": {"node_1": {"type": "conversation", "config": {...}}}, "edges": [{"source": "__start__", "target": "node_1"}]}`

#### Scenario: Convert condition with branching edges — 转换带分支边的条件节点
- **WHEN** canvas has a condition node with two outgoing edges labeled "true" and "false"
- **THEN** the output DSL edge has `target: {"true": "node-a", "false": "node-b"}`
- **当**画布上有一个条件节点带有两条标记为"true"和"false"的出边
- **则**输出 DSL 边的 `target` 为 `{"true": "node-a", "false": "node-b"}`

### Requirement: Convert Graph DSL to React Flow state — 将 Graph DSL 转换为 React Flow 状态
The system SHALL provide a function `dslToFlow(dsl: GraphDsl) → {nodes: Node[], edges: Edge[]}` that converts a Graph DSL JSON into React Flow node and edge arrays with auto-layout positioning.

系统应提供一个函数 `dslToFlow(dsl: GraphDsl) → {nodes: Node[], edges: Edge[]}`，将 Graph DSL JSON 转换为 React Flow 节点和边数组，并带有自动布局定位。

#### Scenario: Convert DSL with positioned nodes — 转换带有位置的 DSL
- **WHEN** DSL contains nodes with `_position` metadata
- **THEN** nodes are placed at those positions in the canvas
- **当**DSL 包含带有 `_position` 元数据的节点
- **则**节点被放置在画布上的那些位置

#### Scenario: Convert DSL without positions (auto-layout) — 转换无位置的 DSL（自动布局）
- **WHEN** DSL contains nodes without `_position` metadata
- **THEN** nodes are arranged using a top-to-bottom DAG layout algorithm
- **当**DSL 包含没有 `_position` 元数据的节点
- **则**节点使用自上而下的 DAG 布局算法进行排列

### Requirement: Validate DSL on conversion — 转换时验证 DSL
`flowToDsl` SHALL validate the output against `graph-dsl.schema.json` and return validation errors if the DSL is invalid.

`flowToDsl` 应根据 `graph-dsl.schema.json` 验证输出，如果 DSL 无效则返回验证错误。

#### Scenario: Validate missing required node config — 验证缺少必需的节点配置
- **WHEN** a node on canvas has no type configured
- **THEN** `flowToDsl` returns an error list including "Node 'x' is missing required field 'type'"
- **当**画布上的节点没有配置类型
- **则** `flowToDsl` 返回错误列表，包含"节点 'x' 缺少必需字段 'type'"

#### Scenario: Validate unreachable nodes — 验证不可达节点
- **WHEN** a node exists on canvas with no path from `__start__`
- **THEN** `flowToDsl` returns a warning "Node 'x' is unreachable from entry point" but does not fail
- **当**画布上存在无法从 `__start__` 到达的节点
- **则** `flowToDsl` 返回警告"节点 'x' 从入口点不可达"但不会失败

### Requirement: Round-trip fidelity — 往返保真度
Converting DSL → Flow → DSL SHALL produce semantically equivalent DSL (node types, configs, edges match). Visual metadata (positions, viewport) MAY differ.

DSL → Flow → DSL 的转换应产生语义等价的 DSL（节点类型、配置、边匹配）。可视化元数据（位置、视口）可能不同。

#### Scenario: Round-trip preserves node configs — 往返转换保留节点配置
- **WHEN** DSL with a conversation node having `{"model": "gpt-4o", "system_prompt": "You are helpful"}` is converted to flow and back
- **THEN** the resulting DSL contains the same node with the same model and system_prompt values
- **当**包含 conversation 节点（配置为 `{"model": "gpt-4o", "system_prompt": "You are helpful"}`）的 DSL 被转换为 flow 再转回 DSL
- **则**最终的 DSL 包含相同的节点，具有相同的 model 和 system_prompt 值
