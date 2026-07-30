## ADDED Requirements — 新增需求

### Requirement: Compile-time channel access validation — 编译时通道访问验证
`GraphCompiler.compile()` 应验证每个节点声明的 `channels.readable` 和 `channels.writable` 列表引用了图谱 `state` 声明中存在的通道。当节点声明访问不存在的通道时，编译器应记录一个 WARNING。当没有通道声明的节点通过边连接到通道时，编译器还应发出警告，建议显式声明通道访问。

#### Scenario: Node declares readable channel that does not exist — 节点声明不存在的可读通道
- **当** 节点声明 `channels.readable: ["nonexistent"]` 且 "nonexistent" 不在图谱的 `state` 中
- **则** 编译器应记录 WARNING："Node '{node_id}' declares readable channel 'nonexistent' which is not defined in graph state"

#### Scenario: Node declares writable channel that does not exist — 节点声明不存在的可写通道
- **当** 节点声明 `channels.writable: ["nonexistent"]` 且 "nonexistent" 不在图谱的 `state` 中
- **则** 编译器应记录 WARNING："Node '{node_id}' declares writable channel 'nonexistent' which is not defined in graph state"

#### Scenario: Node with no channel declaration produces no warning — 无通道声明的节点不产生警告
- **当** 节点没有 `channels` 配置（既无可读也无可写）
- **则** 编译器不应为该节点发出任何通道访问警告

#### Scenario: All declared channels exist in state — 所有声明通道存在于状态中
- **当** 节点声明 `channels.readable: ["messages"]` 和 `channels.writable: ["messages"]` 且 "messages" 在图谱 `state` 中已定义
- **则** 编译器不应为该节点发出任何通道访问警告

### Requirement: Runtime channel access warning — 运行时通道访问警告
`ChannelManager.read()` 和 `ChannelManager.write()` 方法应接受一个可选的 `node_id` 参数。当提供时，该方法应通过已编译图谱的通道访问映射检查节点是否已声明对该通道的访问。如果节点未声明访问，应记录一个 WARNING。

#### Scenario: Node reads from undeclared channel — 节点从未声明的通道读取
- **当** 调用 `ChannelManager.read("messages", node_id="agent_a")` 且 "agent_a" 未在其 `readable` 列表中声明 "messages"
- **则** 应记录 WARNING："Node 'agent_a' reads from channel 'messages' without declaring it as readable"

#### Scenario: Node writes to undeclared channel — 节点写入未声明的通道
- **当** 调用 `ChannelManager.write("results", value, node_id="agent_b")` 且 "agent_b" 未在其 `writable` 列表中声明 "results"
- **则** 应记录 WARNING："Node 'agent_b' writes to channel 'results' without declaring it as writable"

#### Scenario: Node reads from declared channel — 节点从已声明通道读取
- **当** 调用 `ChannelManager.read("messages", node_id="agent_a")` 且 "agent_a" 已在其 `readable` 列表中声明 "messages"
- **则** 不应记录任何警告

#### Scenario: No node_id provided skips check — 未提供 node_id 跳过检查
- **当** 调用 `ChannelManager.read("messages")` 而不带 `node_id`
- **则** 不应执行任何通道访问检查

### Requirement: CompiledGraph includes channel access map — CompiledGraph 包含通道访问映射
`CompiledGraph` 数据类应包含一个 `channel_access` 字段，将每个节点 ID 映射到其声明的可读和可写通道集合。没有通道声明的节点应具有空集合。

#### Scenario: Channel access map populated from config — 从配置填充通道访问映射
- **当** 图谱中的节点 "agent_a" 具有 `config.channels.readable: ["messages", "context"]` 和 `config.channels.writable: ["messages"]`
- **则** `compiled_graph.channel_access["agent_a"]` 应为 `ChannelAccess(readable={"messages", "context"}, writable={"messages"})`

#### Scenario: Node without channel config has empty access — 无通道配置的节点具有空访问权限
- **当** 图谱中的节点 "condition_1" 没有 `channels` 配置
- **则** `compiled_graph.channel_access["condition_1"]` 应为 `ChannelAccess(readable=set(), writable=set())`
