## Context — 背景

Channel 写入语义分散在 engine 层中。`Channel._initial_value()` 和 `Channel.write()` 使用针对 `ChannelType` 枚举值的 `if/elif` 链。`ChannelManager.write()` 检查 `ChannelType.TOPIC | PERSISTENT_TOPIC` 以判断驱逐资格。`ConflictResolver.resolve()` 使用原始字符串比较（`"last_value"`、`"topic"`、`"accumulator"`），完全没有引用枚举。添加新的 channel 类型需要在 3 个文件的 4 个位置进行协调修改，且没有结构性的保证来确保一致性。

此外，`PERSISTENT_TOPIC` 是一个独立的枚举值，但其运行时行为与 `TOPIC` 完全相同——持久化语义并未实现。持久化与写入行为是正交的，不应作为单独的类型存在。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 将所有 channel 类型特定行为（初始值、写入、驱逐资格、冲突解决）整合到单个 `ChannelBehavior` ABC 中
- 提供将类型名称字符串映射到 `ChannelBehavior` 实例的 `ChannelTypeRegistry`
- 预注册 3 个语义上不同的类型（LAST_VALUE、TOPIC、ACCUMULATOR）
- 将持久化与写入语义分离 — `ChannelDef` 新增 `persistent: bool`
- 保持对使用 `"persistent_topic"` 的现有图定义的向后兼容

**非目标：**
- 实现 `persistent=True` 通道的实际持久化语义（P2）
- 构建用于第三方 channel 行为的插件加载系统
- 改变 `Channel` 序列化/反序列化格式（PERSISTENT_TOPIC 迁移除外）

## Decisions — 设计决策

### D1: ChannelBehavior 是 ABC（而非 Protocol）

**选择**：使用 engine 现有的 ABC 模式（`abc.ABC`、`@abstractmethod`）。

**考虑的替代方案**：
- `typing.Protocol` — 更轻量，但与 engine 约定不一致（所有其他扩展点都使用 ABC：EnginePort、Worker、CheckpointStore 等）

**理由**：与现有的 engine ABC 清单保持一致。ChannelBehavior 遵循与 EvictionPolicy、OptimizationPass 等相同的抽象/具体模式。

### D2: ChannelBehavior 有 4 个抽象方法

```python
class ChannelBehavior(ABC):
    @abstractmethod
    def initial_value(self, defn: ChannelDef) -> Any: ...
    
    @abstractmethod
    def write(self, current: Any, value: Any, defn: ChannelDef) -> Any: ...
    
    @abstractmethod
    def is_evictable(self) -> bool: ...
    
    @abstractmethod
    def resolve_conflict(self, current: Any, proposed: Any) -> Any: ...
```

**理由**：这 4 个方法覆盖了当前检查 channel 类型的每一个位置。`write()` 返回新值（不可变风格），而不是原地修改，这样更利于测试和冲突解决。

### D3: ChannelTypeRegistry 是模块级单例

**选择**：模块级 `_REGISTRY: dict[str, ChannelBehavior]`，带有 `register()`、`get()` 和 `list_types()` 函数。

**考虑的替代方案**：
- 基于类的单例 — 对于本质上就是一个 dict 的情况来说，增加了不必要的间接层
- ChannelManager 上的实例 — 每个 manager 都需要接收和转发 registry

**理由**：与 `services/context/provider_shaping.py` 中的现有 `_STRATEGY_REGISTRY` 模式匹配。模块级 registry 简单、可测试，且与项目现有模式一致。

### D4: PERSISTENT_TOPIC 成为已弃用的别名

**选择**：`parse_graph()` 自动将 `"persistent_topic"` 迁移为 `"topic"` 并附带 `persistent=True`。`ChannelType` 枚举保留 `PERSISTENT_TOPIC = "persistent_topic"` 以保持向后兼容，但它在 registry 中映射到 `TopicBehavior`。

**考虑的替代方案**：
- 完全移除 `PERSISTENT_TOPIC`（BREAKING 变更，没有迁移路径）
- 保留 `PERSISTENT_TOPIC` 作为独立行为（无法解决混为一谈的问题）

**理由**：迁移路径保留了现有的图定义。枚举值仍然存在，因此引用它的代码可以编译，但 registry 将其映射到与 TOPIC 相同的行为。

### D5: ChannelDef 新增 persistent: bool

**选择**：在 `ChannelDef` 数据类中添加 `persistent: bool = False` 字段。JSON Schema 新增 `"persistent"` 布尔属性。驱逐、写入和冲突解决忽略此标志（持久化由 checkpoint 层处理）。

**理由**：持久化是一个存储问题，而不是写入语义问题。将它们分离后，任何 channel 类型都可以在将来支持持久化。

### D6: ConflictResolver 委托给 ChannelBehavior

**选择**：`ConflictResolver.resolve()` 接受 `ChannelBehavior` 而不是 `channel_type: str`。调用者（PregelRuntime）从 registry 中查找行为。

**理由**：消除了重复 channel 类型逻辑的字符串分发。冲突解决现在由驱动写入的相同行为对象驱动。

## Risks / Trade-offs — 风险与权衡

| 风险 | 缓解措施 |
|------|---------|
| 行为对象是无状态的 — `write()` 接受 `(current, value, defn)` 并返回新值。Channel 必须存储结果。 | 干净的函数式接口；易于测试 |
| 模块级单例使测试稍显困难 | 测试可以调用 `register()` 覆盖；或使用测试特定的设置 |
| `parse_graph()` 中的 `persistent_topic` 迁移增加了复杂性 | 一次性自动迁移，附带弃用警告；简单的字符串替换 |
| ~10 个测试文件需要更新 ChannelType 引用 | 机械性修改；每个测试只需使用新 API |
