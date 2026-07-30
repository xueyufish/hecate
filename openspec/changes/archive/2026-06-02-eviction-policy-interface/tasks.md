## 1. EvictionPolicy ABC — EvictionPolicy ABC

- [x] 1.1 创建 `src/hecate/engine/eviction.py`，包含定义抽象方法的 `EvictionPolicy(ABC)`：`should_evict(channel_name: str, current_size: int, context: dict) -> bool`、`select_victim(items: list, max_count: int) -> list`
- [x] 1.2 为 EvictionPolicy ABC 和每个抽象方法添加完整的文档字符串

## 2. NoEviction Implementation — NoEviction 实现

- [x] 2.1 实现 `NoEviction(EvictionPolicy)`，should_evict 始终返回 False
- [x] 2.2 `select_victim` 原样返回项目
- [x] 2.3 添加文档字符串

## 3. SizeBasedEviction Implementation — SizeBasedEviction 实现

- [x] 3.1 使用 `max_size: int` 参数实现 `SizeBasedEviction(EvictionPolicy)`
- [x] 3.2 `should_evict` 在 current_size >= max_size 时返回 True
- [x] 3.3 `select_victim` 返回最新的 max_count 个项目（保留最新的）
- [x] 3.4 添加文档字符串

## 4. ChannelManager Integration — ChannelManager 集成

- [x] 4.1 向 `ChannelManager.__init__` 添加可选的 `eviction_policy: EvictionPolicy | None = None` 参数
- [x] 4.2 如果未提供策略，默认为 `NoEviction()`
- [x] 4.3 在 `Channel.write()` 中，向 TOPIC channels 追加后，检查 `should_evict` 并在需要时应用 `select_victim`
- [x] 4.4 仅对 TOPIC/PERSISTENT_TOPIC channels 应用驱逐（不是 LAST_VALUE 或 ACCUMULATOR）

## 5. Tests — 测试

- [x] 5.1 创建 `tests/test_engine/test_eviction.py`
- [x] 5.2 测试 EvictionPolicy 是抽象的
- [x] 5.3 测试 NoEviction.should_evict 始终返回 False
- [x] 5.4 测试 NoEviction.select_victim 返回所有项目
- [x] 5.5 测试 SizeBasedEviction.should_evict 低于/高于阈值的行为
- [x] 5.6 测试 SizeBasedEviction.select_victim 保留最新的项目
- [x] 5.7 测试 ChannelManager 默认使用 NoEviction
- [x] 5.8 测试使用 SizeBasedEviction 的 ChannelManager 在 TOPIC 写入时驱逐

## 6. Verification — 验证

- [x] 6.1 运行 `ruff check src/hecate/engine/eviction.py src/hecate/engine/channel.py tests/test_engine/test_eviction.py`
- [x] 6.2 运行 `ruff format --check src/hecate/engine/eviction.py src/hecate/engine/channel.py tests/test_engine/test_eviction.py`
- [x] 6.3 运行 `mypy src/hecate/engine/eviction.py src/hecate/engine/channel.py`
- [x] 6.4 运行 `python -m pytest tests/test_engine/test_eviction.py -v`
- [x] 6.5 运行完整测试套件 `python -m pytest tests/ -q` 以验证无回归