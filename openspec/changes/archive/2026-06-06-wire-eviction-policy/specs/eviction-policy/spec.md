## MODIFIED Requirements — 修改的需求

### Requirement: ChannelManager 接受可选的驱逐策略 — ChannelManager 接受可选的驱逐策略
ChannelManager SHALL 在其构造函数中接受可选的 `eviction_policy` 参数，默认为 `NoEviction()`。每次向 TOPIC 或 PERSISTENT_TOPIC channel 写入后，ChannelManager SHALL 调用 `eviction_policy.should_evict()`。如果需要驱逐，ChannelManager SHALL 将 channel 的值替换为 `eviction_policy.select_victim()` 的结果。

#### Scenario: 默认驱逐策略
- **WHEN** ChannelManager 在未指定 eviction_policy 的情况下创建
- **THEN** 它 SHALL 内部使用 `NoEviction()` 并且从不驱逐

#### Scenario: 使用自定义驱逐策略的 TOPIC channel
- **WHEN** 使用 `SizeBasedEviction(max_size=3)` 创建 ChannelManager，且名为 "messages" 的 TOPIC channel 有 4 个项目
- **THEN** 写入第 5 个项目后，ChannelManager SHALL 调用 should_evict("messages", 5, {})，返回 True
- **AND** ChannelManager SHALL 调用 select_victim([所有 5 个项目])，返回最新的 3 个项目

#### Scenario: LAST_VALUE channel 不受驱逐影响
- **WHEN** 使用 `SizeBasedEviction(max_size=3)` 创建 ChannelManager 并向 LAST_VALUE channel 写入
- **THEN** 驱逐 SHALL 不会被应用（只有 TOPIC 和 PERSISTENT_TOPIC channel 触发驱逐检查）

#### Scenario: ACCUMULATOR channel 不受驱逐影响
- **WHEN** 使用 `SizeBasedEviction(max_size=3)` 创建 ChannelManager 并向 ACCUMULATOR channel 写入
- **THEN** 驱逐 SHALL 不会被应用

#### Scenario: 恢复不触发驱逐
- **WHEN** 调用 ChannelManager.restore(state)，状态中包含有 10 个项目的 TOPIC channel
- **THEN** channel SHALL 接收全部 10 个项目，没有任何驱逐，无论驱逐策略如何
