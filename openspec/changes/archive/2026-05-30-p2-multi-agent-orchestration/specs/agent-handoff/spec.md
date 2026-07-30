## 新增需求

### 需求：Handoff 工具自动注入
当 Graph DSL 定义了从该 agent 节点发出的 handoff 边时，系统须将 `handoff_to_agent` 工具注入到 Agent 的工具列表中。该工具须接受一个指定目标 agent 节点 ID 的 `target` 参数。

#### 场景：存在 handoff 边时注入 handoff 工具
- **当** Graph DSL 包含从 agent 节点 "triage" 到 agent 节点 "billing" 且带有 `type: "handoff"` 的边
- **则** 系统将 `handoff_to_agent` 工具注入到 "triage" agent 的工具列表中，`target` 参数接受值 `["billing"]`

#### 场景：无 handoff 边时不注入 handoff 工具
- **当** Graph DSL 没有从某 agent 节点出发的 `type: "handoff"` 边
- **则** 系统不将 `handoff_to_agent` 工具注入到该 agent 的工具列表中

### 需求：Handoff 工具执行产生 Command(goto)
系统须通过返回带有 `Command(goto=target_node_id)` 的 `WorkerResult` 来执行 `handoff_to_agent` 工具。Pregel 运行时须将下一个节点解析为目标 agent 节点。

#### 场景：成功执行 handoff
- **当** LLM 调用 `handoff_to_agent(target="billing")`
- **则** worker 返回 `Command(goto="billing")` 且 Pregel 运行时在下一个 superstep 执行 "billing" agent 节点

#### 场景：带对话上下文的 Handoff
- **当** LLM 调用 `handoff_to_agent(target="specialist")` 且当前 channel 有包含对话历史的 `messages`
- **则** 目标 agent 节点接收对话消息作为其输入上下文

### 需求：Graph DSL 中的 Handoff 边类型
系统须支持 Graph DSL 中边上的可选 `type` 字段。当 `type: "handoff"` 时，该边表示控制转移（handoff）。当不存在时，该边为标准数据流边。

#### 场景：解析包含 handoff 边的图
- **当** `parse_graph()` 接收带有 `{"source": "agent_a", "target": "agent_b", "type": "handoff"}` 的 DSL
- **则** 生成的 `Edge` 设置 `trigger="handoff"`

#### 场景：编译包含 handoff 边的图
- **当** `GraphCompiler.compile()` 处理包含 handoff 边的图
- **则** 编译后的图保留 handoff 边触发，且编译器验证源和目标都是 agent 类型节点

### 需求：Handoff 循环检测
系统须在图编译期间检测并拒绝循环 handoff 链。当一系列 handoff 边形成循环时，即存在循环 handoff 链。

#### 场景：检测到循环 handoff
- **当** 图包含 handoff 边 A→B, B→C, C→A 形成循环
- **则** 编译器抛出 `GraphCompilationError`，附带列出该循环的消息

#### 场景：非循环 handoff 被接受
- **当** 图包含 handoff 边 A→B, B→C（无循环）
- **则** 编译器接受该图，无错误
