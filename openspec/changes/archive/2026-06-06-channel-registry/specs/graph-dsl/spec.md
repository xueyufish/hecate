## MODIFIED Requirements — 修改的需求

### Requirement: Graph DSL 解析器验证 JSON Schema
`parse_graph()` 函数 SHALL 接受 JSON 字符串或字典，并根据 `schemas/graph-dsl.schema.json` 进行验证。schema SHALL 在 channel 定义中包含 `"persistent"` 作为可选的布尔属性。解析器 SHALL 自动将已弃用的 `"persistent_topic"` 迁移为 `"topic"` 并附带 `persistent=True`。

#### Scenario: JSON 中的持久化 channel
- **WHEN** `parse_graph()` 遇到包含 `"type": "topic", "persistent": true` 的 channel 定义
- **THEN** 它 SHALL 创建 `ChannelDef(type=ChannelType.TOPIC, persistent=True)`

#### Scenario: 已弃用的 persistent_topic
- **WHEN** `parse_graph()` 遇到 `"type": "persistent_topic"`
- **THEN** 它 SHALL 创建 `ChannelDef(type=ChannelType.TOPIC, persistent=True)` 并记录弃用警告

#### Scenario: 自定义注册类型
- **WHEN** `parse_graph()` 遇到 `"type": "priority_queue"` 且 "priority_queue" 已在 ChannelTypeRegistry 中注册
- **THEN** 它 SHALL 创建 `ChannelDef(type=ChannelType("priority_queue"))` 且不报错

#### Scenario: 未知类型
- **WHEN** `parse_graph()` 遇到 `"type": "unknown"` 且 "unknown" 不在 registry 中
- **THEN** 它 SHALL 引发 `GraphValidationError`，字段指向该 channel 类型
