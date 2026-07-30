## ADDED Requirements — 新增需求

### Requirement：EvictionPolicy ABC 定义可插拔的 channel 驱逐 — EvictionPolicy ABC 定义可插拔的 channel 驱逐
引擎 SHALL 在 `engine/eviction.py` 中定义一个 `EvictionPolicy` ABC，包含方法：`should_evict` 和 `select_victim`。

#### Scenario：检查是否需要驱逐
- **WHEN** 调用 `should_evict(channel_name, current_size, context)`
- **THEN** 如果需要驱逐，它 SHALL 返回 `True`，否则返回 `False`

#### Scenario：选择要保留的项目
- **WHEN** 使用项目列表和最大数量调用 `select_victim(items, max_count)`
- **THEN** 它 SHALL 返回要保留的项目列表（其余被驱逐）

### Requirement：NoEviction 保留当前无限制行为 — NoEviction 保留当前无限制行为
一个 `NoEviction` SHALL 通过从不驱逐来实现 EvictionPolicy（始终返回 False）。

#### Scenario：NoEviction 从不驱逐
- **WHEN** 在 NoEviction 上调用 `should_evict("messages", 10000, {})`
- **THEN** 它 SHALL 返回 `False`

#### Scenario：NoEviction 保留所有项目
- **WHEN** 在 NoEviction 上调用 `select_victim(items, 5)`
- **THEN** 它 SHALL 原样返回所有项目

### Requirement：SizeBasedEviction 在大小超过最大值时驱逐最旧的项目 — SizeBasedEviction 在大小超过最大值时驱逐最旧的项目
一个 `SizeBasedEviction` SHALL 通过在 channel 大小超过配置的最大值时驱逐最旧的项目来实现 EvictionPolicy。

#### Scenario：低于最大大小
- **WHEN** 使用 max_size=100 调用 `should_evict("messages", 50, {})`
- **THEN** 它 SHALL 返回 `False`

#### Scenario：等于最大大小
- **WHEN** 使用 max_size=100 调用 `should_evict("messages", 100, {})`
- **THEN** 它 SHALL 返回 `True`

#### Scenario：超过最大大小
- **WHEN** 使用 max_size=100 调用 `should_evict("messages", 150, {})`
- **THEN** 它 SHALL 返回 `True`

#### Scenario：保留最新的项目
- **WHEN** 使用 `select_victim(["a", "b", "c", "d", "e"], 3)`
- **THEN** 它 SHALL 返回 `["c", "d", "e"]`（保留最后 3 个）

### Requirement：ChannelManager 接受可选的驱逐策略 — ChannelManager 接受可选的驱逐策略
ChannelManager SHALL 接受可选的 `eviction_policy` 参数，默认为 `NoEviction()`。

#### Scenario：默认驱逐策略
- **WHEN** 创建未指定 eviction_policy 的 ChannelManager
- **THEN** 它 SHALL 内部使用 `NoEviction()`

#### Scenario：自定义驱逐策略
- **WHEN** 使用 SizeBasedEviction(max_size=100) 创建 ChannelManager
- **THEN** 它 SHALL 在 TOPIC channel 写入大小超过 100 时应用驱逐