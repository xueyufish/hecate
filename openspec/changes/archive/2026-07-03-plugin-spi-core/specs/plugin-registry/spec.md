## ADDED Requirements — 新增需求

### Requirement: PluginRegistry 注册 — PluginRegistry registration
系统应提供一个管理插件注册和发现的 `PluginRegistry` 类。注册表必须支持：
- `register(manifest: PluginManifest, plugin: Any) -> None` — 使用清单注册插件
- `unregister(type: str, name: str) -> None` — 移除已注册的插件
- `get_by_type(type: str) -> dict[str, Any]` — 获取给定类型的所有插件，以名称为键
- `get_by_name(type: str, name: str) -> Any | None` — 按类型和名称获取特定插件
- `list_all() -> dict[str, dict[str, Any]]` — 获取按类型分组的所有已注册插件

#### Scenario: 注册插件 — Register a plugin
- **WHEN** 开发者调用 `registry.register(manifest, plugin_instance)`
- **THEN** 插件被存储，可按类型和名称检索

#### Scenario: 注册重复插件 — Register duplicate plugin
- **WHEN** 开发者注册与现有插件相同类型和名称的插件
- **THEN** 新插件替换旧插件

#### Scenario: 注销插件 — Unregister a plugin
- **WHEN** 开发者调用 `registry.unregister(type, name)`
- **THEN** 插件被移除，不再可检索

#### Scenario: 按类型获取插件 — Get plugins by type
- **WHEN** 开发者调用 `registry.get_by_type("evaluator")`
- **THEN** 返回以名称为键的所有评估器插件的字典

#### Scenario: 获取特定插件 — Get specific plugin
- **WHEN** 开发者调用 `registry.get_by_name("evaluator", "faithfulness")`
- **THEN** 返回特定的评估器插件，如果未找到则返回 None

#### Scenario: 列出所有插件 — List all plugins
- **WHEN** 开发者调用 `registry.list_all()`
- **THEN** 返回按类型分组的所有插件的字典

### Requirement: PluginRegistry 线程安全 — PluginRegistry thread safety
系统应确保 PluginRegistry 对并发注册和查找操作是线程安全的。

#### Scenario: 并发注册 — Concurrent registration
- **WHEN** 多个线程同时注册插件
- **THEN** 所有注册完成，无数据损坏

#### Scenario: 注册期间的并发查找 — Concurrent lookup during registration
- **WHEN** 一个线程注册插件而另一个线程按类型查询
- **THEN** 查询返回一致的快照，不抛出异常
