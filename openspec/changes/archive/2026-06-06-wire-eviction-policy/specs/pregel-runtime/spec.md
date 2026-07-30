## MODIFIED Requirements — 修改的需求

### Requirement: PregelRuntime 接受可选的驱逐策略 — PregelRuntime 接受可选的驱逐策略
PregelRuntime SHALL 接受可选的 `eviction_policy: EvictionPolicy | None = None` 构造函数参数。当为 None 时，它 SHALL 默认为 `NoEviction()`。驱逐策略 SHALL 被传递给 ChannelManager 构造函数。

#### Scenario: 默认驱逐策略
- **WHEN** PregelRuntime 在未指定 eviction_policy 的情况下创建
- **THEN** 内部的 ChannelManager SHALL 使用 `NoEviction()`

#### Scenario: 自定义驱逐策略
- **WHEN** PregelRuntime 使用 `eviction_policy=SizeBasedEviction(max_size=100)` 创建
- **THEN** 内部的 ChannelManager SHALL 对所有 TOPIC channel 写入使用提供的驱逐策略
