## Context — 背景

ChannelManager 在内存中存储 channel 值。TOPIC channels 向列表中追加内容，在长时间运行的会话中可能无限制增长。目前没有限制内存使用或驱逐陈旧数据的机制。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 定义包含 `should_evict` 和 `select_victim` 方法的 `EvictionPolicy` ABC
- 提供 `NoEviction`（默认，保留当前行为）和 `SizeBasedEviction` 实现
- 使驱逐成为 ChannelManager 的可选参数
- 保持引擎零依赖

**非目标：**
- 基于时间的驱逐（P3+）
- 跨节点的分布式驱逐
- 在 checkpoint 恢复期间驱逐

## Decisions — 设计决策

### D1：EvictionPolicy 是引擎内部的

**选择**：创建 `engine/eviction.py`，与 `engine/channel.py` 并列。

**理由**：驱逐是一个状态管理问题，不是服务边界。

### D2：双方法接口

**选择**：`should_evict(channel_name, current_size, context) -> bool` 和 `select_victim(items, max_count) -> list`（返回要保留的项目）。

**理由**：将决策（是否应该驱逐）与选择（移除哪些项目）分开，允许灵活的策���。

### D3：SizeBasedEviction 保留最新的项目

**选择**：当大小超过最大值时，驱逐最旧的项目（保留最新的 `max_size` 个）。

**理由**：对于对话历史，最近的消息比旧消息更相关。

## Risks / Trade-offs — 风险与权衡

| 风险 | 缓解措施 |
|------|---------|
| 驱逐移除可能需要的某些数据 | NoEviction 是默认值；SizeBasedEviction 是可选加入的 |
| 每次写入时驱逐增加开销 | should_evict 是 O(1)；仅当需要时调用 select_victim |