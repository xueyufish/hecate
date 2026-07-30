## ADDED Requirements — 新增需求

### Requirement: PluginLifecycle 协议 — PluginLifecycle protocol
系统应定义一个 `PluginLifecycle` 协议，插件可以实现该协议以提供生命周期钩子。协议必须包含：
- `on_load() -> None` — 插件注册时调用
- `on_unload() -> None` — 插件注销时调用

#### Scenario: 插件实现生命周期 — Plugin implements lifecycle
- **WHEN** 插件实现了 PluginLifecycle 协议
- **THEN** PluginRegistry 在注册后调用 on_load()，在注销后调用 on_unload()

#### Scenario: 插件未实现生命周期 — Plugin does not implement lifecycle
- **WHEN** 插件未实现 PluginLifecycle 协议
- **THEN** PluginRegistry 记录调试消息并继续，不调用生命周期钩子

### Requirement: PluginRegistry 生命周期集成 — PluginRegistry lifecycle integration
系统应将 PluginLifecycle 钩子集成到 PluginRegistry 的注册和注销流程中。

#### Scenario: 注册触发 on_load — Registration triggers on_load
- **WHEN** 注册实现了 PluginLifecycle 的插件
- **THEN** PluginRegistry 在插件实例上调用 on_load()

#### Scenario: 注销触发 on_unload — Unregistration triggers on_unload
- **WHEN** 注销实现了 PluginLifecycle 的插件
- **THEN** PluginRegistry 在移除前在插件实例上调用 on_unload()

#### Scenario: 生命周期钩子异常处理 — Lifecycle hook exception handling
- **WHEN** 生命周期钩子抛出异常
- **THEN** PluginRegistry 记录错误并继续（不传播异常）
