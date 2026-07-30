## 1. ChannelManager 驱逐接入

- [x] 1.1 修改 `src/hecate/engine/channel.py` 中的 `ChannelManager.__init__()` — 添加 `eviction_policy: EvictionPolicy | None = None` 参数，存储为 `self._eviction_policy = eviction_policy or NoEviction()`，从 `hecate.engine.eviction` 导入
- [x] 1.2 修改 `ChannelManager.write()` — 在 `self._channels[name].write(value)` 之后，检查 channel 类型是否为 TOPIC 或 PERSISTENT_TOPIC；如果是，获取 `channel = self._channels[name]`，调用 `self._eviction_policy.should_evict(name, len(channel._value), {})`，如果为 True 则设置 `channel._value = self._eviction_policy.select_victim(channel._value)`

## 2. PregelRuntime 传递

- [x] 2.1 修改 `src/hecate/engine/pregel.py` 中的 `PregelRuntime.__init__()` — 添加 `eviction_policy: EvictionPolicy | None = None` 参数，从 `hecate.engine.eviction` 导入
- [x] 2.2 将 `self._channel_manager = ChannelManager()` 改为 `self._channel_manager = ChannelManager(eviction_policy=eviction_policy or NoEviction())`

## 3. 测试

- [x] 3.1 在 `tests/test_engine/test_eviction.py` 中添加测试 `test_channel_manager_default_no_eviction` — 创建不带 eviction_policy 的 ChannelManager，向 TOPIC channel 写入 100 个项目，验证全部 100 个存在
- [x] 3.2 添加测试 `test_channel_manager_size_based_eviction` — 使用 SizeBasedEviction(max_size=5) 创建 ChannelManager，向 TOPIC channel 写入 10 个项目，验证仅保留最新的 5 个
- [x] 3.3 添加测试 `test_channel_manager_eviction_skips_last_value` — 使用 SizeBasedEviction(max_size=3) 创建 ChannelManager，多次写入 LAST_VALUE channel，验证所有写入成功且没有驱逐
- [x] 3.4 添加测试 `test_channel_manager_eviction_skips_accumulator` — 使用 SizeBasedEviction(max_size=3) 创建 ChannelManager，写入 ACCUMULATOR channel，验证值为所有写入的总和
- [x] 3.5 添加测试 `test_channel_manager_restore_bypasses_eviction` — 使用 SizeBasedEviction(max_size=3) 创建 ChannelManager，恢复具有 10 个项目的 TOPIC channel 快照，验证全部 10 个存在（无驱逐）
- [x] 3.6 在 `tests/test_engine/test_pregel.py` 中添加测试 `test_pregel_runtime_eviction_passthrough` — 使用 SizeBasedEviction(max_size=3) 创建 PregelRuntime，执行向 TOPIC channel 写入 5 个项目的图，验证 channel 仅有最新的 3 个项目

## 4. 验证

- [x] 4.1 运行 `ruff check src/hecate/ tests/`
- [x] 4.2 运行 `ruff format --check src/ tests/`
- [x] 4.3 运行 `mypy src/`
- [x] 4.4 运行 `python -m pytest tests/ -q` — 无回归
