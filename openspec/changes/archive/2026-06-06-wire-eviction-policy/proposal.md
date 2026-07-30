## Why — 动机

EvictionPolicy ABC 及其实现（NoEviction、SizeBasedEviction）已定义，但未接入 ChannelManager。在长时间运行的会话中，TOPIC channels 会无限制增长，没有限制内存使用的机制。`openspec/specs/eviction-policy/spec.md` 中的规范已经定义了集成契约（ChannelManager 接受可选的 eviction_policy，在 TOPIC 写入时应用）——此变更实现了该接入。

## What Changes — 变更内容

- 将 EvictionPolicy 接入 ChannelManager：接受可选的 `eviction_policy` 参数（默认 `NoEviction`），在 TOPIC/PERSISTENT_TOPIC 写入后应用驱逐
- 将 EvictionPolicy 接入 PregelRuntime：接受可选的 `eviction_policy` 参数，传递给 ChannelManager 构造函数
- 不要在 `ChannelManager.restore()` 期间应用驱逐——恢复必须重现精确的 checkpoint 状态
- 添加接入集成测试：带驱逐的 ChannelManager、带驱逐的 PregelRuntime

## Capabilities — 能力变更

### 新增能力

（无）

### 修改的能力

- `eviction-policy`: 添加要求 ChannelManager 和 PregelRuntime 接受并使用 EvictionPolicy（当前规范仅涵盖 ABC 定义，未涉及接入）
- `pregel-runtime`: 添加可选的 `eviction_policy` 构造函数参数

## Impact — 影响范围

- `src/hecate/engine/channel.py`: ChannelManager 构造函数 + write 方法
- `src/hecate/engine/pregel.py`: PregelRuntime 构造函数（传递 eviction_policy）
- `tests/test_engine/test_eviction.py`: 添加 ChannelManager 驱逐集成测试
- `tests/test_engine/test_pregel.py`: 添加启用驱逐的 PregelRuntime 测试
