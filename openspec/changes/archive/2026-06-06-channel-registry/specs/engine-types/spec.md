## MODIFIED Requirements — 修改的需求

### Requirement: ChannelDef 包含持久化标志 — ChannelDef 包含持久化标志
`ChannelDef` 数据类 SHALL 包含一个 `persistent: bool = False` 字段。`ChannelType` 枚举 SHALL 保留 `PERSISTENT_TOPIC` 以保持向后兼容，但 registry SHALL 将其映射到 `TopicBehavior`。

#### Scenario: 默认非持久化
- **WHEN** 创建 `ChannelDef(type=ChannelType.TOPIC)`
- **THEN** `persistent` SHALL 为 `False`

#### Scenario: 显式持久化
- **WHEN** 创建 `ChannelDef(type=ChannelType.TOPIC, persistent=True)`
- **THEN** `persistent` SHALL 为 `True`

#### Scenario: PERSISTENT_TOPIC 自动迁移
- **WHEN** `parse_graph()` 在图定义中遇到 `"type": "persistent_topic"`
- **THEN** 它 SHALL 创建 `ChannelDef(type=ChannelType.TOPIC, persistent=True)` 并记录弃用警告
