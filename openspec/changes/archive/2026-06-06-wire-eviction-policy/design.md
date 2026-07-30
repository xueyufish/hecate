## Context — 背景

EvictionPolicy ABC（NoEviction、SizeBasedEviction）已在 `engine/eviction.py` 中实现并具有完整的测试覆盖。ChannelManager（`engine/channel.py`）管理类型化 channel，但没有驱逐机制。`openspec/specs/eviction-policy/spec.md` 中的规范已经定义了接入契约——ChannelManager 接受可选的 `eviction_policy` 参数——但代码尚未实现。

该模式与已完成的 SchedulerStrategy 接入完全相同：PregelRuntime 接受可选参数，默认为无操作实现，并将其传递给内部组件。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 将 EvictionPolicy 接入 ChannelManager 构造函数和 write 方法
- 将 EvictionPolicy 接入 PregelRuntime 构造函数（传递给 ChannelManager）
- 仅对 TOPIC 和 PERSISTENT_TOPIC channel 应用驱逐（LAST_VALUE 覆盖，ACCUMULATOR 归约——两者都不会无限制增长）
- 在恢复期间保持精确的 checkpoint 状态（恢复时不执行驱逐）

**非目标：**
- 每个 channel 的驱逐配置（P1 仅全局策略）
- 基于时间或基于 token 的驱逐策略
- 向 should_evict 注入上下文（传递空字典 `{}`）
- 跨节点分布式驱逐

## Decisions — 设计决策

### D1: 注入点为 ChannelManager.__init__

**选择**：ChannelManager 接受 `eviction_policy: EvictionPolicy | None = None`，默认为 `NoEviction()`。

**理由**：镜像 SchedulerStrategy 模式。ChannelManager 是执行写入的状态容器，因此它拥有驱逐决策权。

### D2: 驱逐在写入后触发，仅针对 TOPIC channel

**选择**：在 `ChannelManager.write()` 中，在 `channel.write(value)` 之后，检查 channel 类型是否为 TOPIC/PERSISTENT_TOPIC。如果是，调用 `should_evict()`。如果为 True，将 `channel._value` 替换为 `select_victim()` 的结果。

**理由**：只有 TOPIC channel 会无限制增长（仅追加列表）。LAST_VALUE 覆盖，ACCUMULATOR 归约。驱逐仅对有列表值的 channel 有意义。

### D3: 恢复期间不驱逐

**选择**：不修改 `ChannelManager.restore()`。它直接设置 `_value` 以重现精确的 checkpoint 状态。

**理由**：restore() 有意绕过写入语义。恢复期间的驱逐会破坏 checkpoint 状态。

### D4: PregelRuntime 传递

**选择**：PregelRuntime.__init__ 接受 `eviction_policy: EvictionPolicy | None = None` 并将其传递给 `ChannelManager(eviction_policy=eviction_policy or NoEviction())`。

**理由**：与 SchedulerStrategy 和 conflict_resolver 的模式完全相同——构造函数注入，默认值为 None。

## Risks / Trade-offs — 风险与权衡

| 风险 | 缓解措施 |
|------|---------|
| 驱逐移除了下游节点需要的数据 | NoEviction 是默认值；SizeBasedEviction 为可选加入 |
| 每次 TOPIC 写入时驱逐增加开销 | should_evict 是 O(1)；仅当超过阈值时才调用 select_victim |
| Channel._value 被直接访问（不是通过方法） | 可接受——驱逐是 ChannelManager 内部的状态管理问题 |
