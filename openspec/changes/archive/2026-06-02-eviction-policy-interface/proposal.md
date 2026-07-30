## Why — 动机

ChannelManager 当前在内存中存储 channel 状态，没有驱逐机制。TOPIC channels（仅追加列表）在长时间运行的会话中会无限制增长，最终导致 OOM。EvictionPolicy 接口允许在不修改 ChannelManager 的情况下使用可插拔的驱逐策略。

## What Changes — 变更内容

- 在 `engine/eviction.py` 中添加 `EvictionPolicy` ABC，包含判断何时以及驱逐哪些项目的方法
- 添加 `NoEviction` 实现（当前行为——从不驱逐）
- 添加 `SizeBasedEviction` 实现（当列表超过最大大小时驱逐最旧的项目）
- 将 EvictionPolicy 注册为 ChannelManager 的可选参数
- 不修改现有的 Channel 行为——EvictionPolicy 是附加性的

## Capabilities — 能力变更

### 新增能力
- `eviction-policy`: 用于 channel 状态管理的可插拔驱逐接口

### 修改的能力
- 无

## Impact — 影响范围

- **新文件**: `src/hecate/engine/eviction.py`（ABC + NoEviction + SizeBasedEviction）
- **修改的文件**: `src/hecate/engine/channel.py`（添加可选的驱逐参数）
- **新测试**: `tests/test_engine/test_eviction.py`
- **无破坏性变更**: 现有行为通过默认值保留
- **无新依赖**: 仅使用 stdlib