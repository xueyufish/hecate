## 1. ChannelBehavior ABC

- [x] 1.1 在 `src/hecate/engine/channel.py` 中定义 `ChannelBehavior` ABC，包含抽象方法：`initial_value(defn) -> Any`、`write(current, value, defn) -> Any`、`is_evictable() -> bool`、`resolve_conflict(current, proposed) -> Any`
- [x] 1.2 实现 `LastValueBehavior` — `initial_value` 返回 `defn.default or None`，`write` 返回 `value`，`is_evictable` 返回 `False`，`resolve_conflict` 返回 `proposed`（最后写入者胜出）
- [x] 1.3 实现 `TopicBehavior` — `initial_value` 返回 `[]`，`write` 追加/扩展，`is_evictable` 返回 `True`，`resolve_conflict` 去重并合并列表
- [x] 1.4 实现 `AccumulatorBehavior` — `initial_value` 返回 `defn.initial or 0`，`write` 累加（对于未知 reduce_fn 则覆盖），`is_evictable` 返回 `False`，`resolve_conflict` 对值求和
- [x] 1.5 为所有 3 个内置行为添加单元测试（规范中的 12 个场景）

## 2. ChannelTypeRegistry

- [x] 2.1 在 `src/hecate/engine/channel.py` 中创建模块级 registry — `_REGISTRY: dict[str, ChannelBehavior]`，包含 `register()`、`get()`、`list_types()` 函数
- [x] 2.2 在模块导入时预注册 "last_value" → `LastValueBehavior()`、"topic" → `TopicBehavior()`、"accumulator" → `AccumulatorBehavior()`、"persistent_topic" → `TopicBehavior()`
- [x] 2.3 添加单元测试：预注册类型存在、get 返回正确行为、get 未知类型引发 KeyError、注册自定义类型有效、"persistent_topic" 映射到 TopicBehavior

## 3. Channel 委托

- [x] 3.1 重构 `Channel._initial_value()`，调用 `get(self.defn.type).initial_value(self.defn)` 替代 if/elif
- [x] 3.2 重构 `Channel.write()`，调用 `get(self.defn.type).write(self._value, value, self.defn)` 并存储结果
- [x] 3.3 重构 `ChannelManager.write()`，检查 `get(channel.defn.type).is_evictable()` 替代 `channel.defn.type in (ChannelType.TOPIC, ChannelType.PERSISTENT_TOPIC)`
- [x] 3.4 验证现有 channel 测试无需修改仍通过（行为完全相同）

## 4. ConflictResolver 委托

- [x] 5.1 更改 `ConflictResolver.resolve()` 签名 — 将 `channel_type: str = "last_value"` 替换为 `behavior: ChannelBehavior | None = None`
- [x] 5.2 当提供 `behavior` 时，委托给 `behavior.resolve_conflict(current, proposed)`；当为 None 时，回退到最后写入者胜出
- [x] 5.3 更新 `PregelRuntime._apply_writes()`，从 registry 查找行为并将其传递给 `ConflictResolver.resolve()`
- [x] 5.4 更新 `tests/test_engine/test_temporal/test_conflict.py` 中的测试，传递行为对象替代字符串
- [x] 5.5 添加自定义行为冲突解决和异常回退的测试

## 5. ChannelDef Persistent 标志

- [x] 4.1 在 `src/hecate/engine/types.py` 的 `ChannelDef` 数据类中添加 `persistent: bool = False` 字段
- [x] 4.2 更新 `src/hecate/engine/graph_dsl.py` 中的 `parse_graph()`，从 JSON 读取 `persistent` 并自动将 `"persistent_topic"` 迁移为 `ChannelType.TOPIC` + `persistent=True`，附带弃用警告
- [x] 4.3 更新 `schemas/graph-dsl.schema.json` — 在 channel 定义中添加 `"persistent": { "type": "boolean" }` 属性；在类型枚举中保留 `"persistent_topic"` 以保持向后兼容
- [x] 4.4 更新 `CompiledGraph.to_json()`，在序列化的 channel 定义中包含 `"persistent"`

## 6. 模板更新

- [x] 6.1 更新 `src/hecate/engine/templates.py` — 将所有 `ChannelType.PERSISTENT_TOPIC` 替换为 `ChannelType.TOPIC, persistent=True`（当前 0 处；PERSISTENT_TOPIC 未在模板中使用，但需验证）
- [x] 6.2 搜索并更新其他引用 `ChannelType.PERSISTENT_TOPIC` 的文件，改用 `ChannelType.TOPIC` + `persistent=True`

## 7. 测试更新

- [x] 7.1 更新 `tests/test_engine/test_pregel.py` — 将所有 `ChannelType.PERSISTENT_TOPIC` 引用改为 `ChannelType.TOPIC` + `persistent=True`
- [x] 7.2 更新 `tests/test_engine/test_eviction.py` — 验证驱逐仍通过 `is_evictable()` 委托正常工作
- [x] 7.3 更新 `tests/test_engine/test_graph_dsl.py` — 为 `persistent` 字段解析、已弃用的 `persistent_topic` 迁移和自定义注册类型解析添加测试
- [x] 7.4 验证所有 1129 个现有测试无回归通过

## 8. 验证

- [x] 8.1 运行 `ruff check src/hecate/ tests/`
- [x] 8.2 运行 `ruff format --check src/ tests/`
- [x] 8.3 运行 `mypy src/`
- [x] 8.4 运行 `python -m pytest tests/ -q` — 无回归
