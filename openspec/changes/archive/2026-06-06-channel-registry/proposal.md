## Why — 动机

Channel 行为（写入语义、初始值、驱逐资格、冲突解决）分散在三个文件中，使用了三种不同的匹配机制（枚举比较、`in` 元组检查、原始字符串相等）。这使得系统变得脆弱——添加新的 channel 类型需要分别在 `Channel._initial_value()`、`Channel.write()`、`ChannelManager.write()` 和 `ConflictResolver.resolve()` 中独立修改，且无法保证它们保持一致。此外，`PERSISTENT_TOPIC` 是一个与 `TOPIC` 行为完全相同的独立类型，因为持久化与写入语义被混为一谈。

## What Changes — 变更内容

- **引入 `ChannelBehavior` ABC** — 将 channel 类型的写入、初始值、驱逐资格和冲突解决封装在单个对象中
- **引入 `ChannelTypeRegistry`** — 将类型名称字符串映射到 `ChannelBehavior` 实例，内置注册 4 种现有类型
- **替换 `Channel` 和 `ChannelManager` 中的 if/elif 分发** — 改为行为委托模式
- **替换 `ConflictResolver` 中的字符串分发** — 改为行为委托模式
- **将 `PERSISTENT_TOPIC` 转换为 `ChannelDef` 上的 `persistent: bool`** — 持久化与写入语义是正交的；`PERSISTENT_TOPIC` 变为 `TOPIC` + `persistent=True`
- **BREAKING 变更**：移除 `ChannelType.PERSISTENT_TOPIC` 枚举值；现有使用 `"persistent_topic"` 的图定义将在 `parse_graph()` 期间自动迁移为 `"topic"` + `persistent: true`
- **更新 JSON Schema** — 在 channel 定义中添加 `persistent` 布尔属性，保留 `persistent_topic` 作为已弃用的别名

## Capabilities — 能力变更

### 新增能力
- `channel-registry`: channel 类型的注册表模式，支持可插拔行为（写入、初始值、驱逐、冲突解决）

### 修改的能力
- `engine-types`: ChannelDef 新增 `persistent: bool` 字段；ChannelType 移除 PERSISTENT_TOPIC
- `graph-dsl`: parse_graph() 将已弃用的 "persistent_topic" 迁移为 "topic" + persistent=True；JSON Schema 已更新

## Impact — 影响范围

- **Engine 层**: `types.py` (ChannelDef, ChannelType), `channel.py` (Channel, ChannelManager), `graph_dsl.py` (parse_graph)
- **Temporal 层**: `conflict.py` (ConflictResolver) — 从字符串分发切换为行为委托
- **运行时**: `pregel.py` — 对 channel 类型字符串传递进行小幅修改
- **模板**: `templates.py` — 所有 PERSISTENT_TOPIC 引用变为 TOPIC + persistent=True
- **JSON Schema**: `schemas/graph-dsl.schema.json` — 添加 `persistent` 属性，保留 `persistent_topic` 作为已弃用项
- **测试**: ~10 个测试文件引用了 ChannelType；全部需要更新
