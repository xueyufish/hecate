## ADDED Requirements — 新增需求

### Requirement: PluginManifest 数据类 — PluginManifest dataclass
系统应定义一个 `PluginManifest` 数据类，描述插件元数据。数据类必须是冻结的（不可变）并包含以下字段：
- `type`: str — 插件类型标识符（例如 "tool"、"evaluator"、"channel"、"auth_provider"、"notifier"）
- `name`: str — 在其类型中唯一的插件名称
- `version`: str — 语义版本字符串（例如 "1.0.0"）
- `api_version`: str — 此插件目标的 API 版本
- `min_platform_version`: str — 所需的最低平台版本
- `description`: str — 人类可读的描述
- `permissions`: list[str] — 所需的权限（例如 ["network:https", "filesystem:read"]）

#### Scenario: 创建 PluginManifest — Create PluginManifest
- **WHEN** 开发者使用所有必需字段创建 PluginManifest 实例
- **THEN** 实例是不可变的（冻结）且所有字段可访问

#### Scenario: 具有可选字段的 PluginManifest — PluginManifest with optional fields
- **WHEN** 开发者仅使用必需字段创建 PluginManifest
- **THEN** 可选字段默认为空字符串或空列表

### Requirement: PluginManifest 相等性和哈希 — PluginManifest equality and hashing
系统应支持基于 type + name + version 的 PluginManifest 实例的相等比较和哈希。

#### Scenario: 比较相等的清单 — Compare equal manifests
- **WHEN** 两个 PluginManifest 实例具有相同的 type、name 和 version
- **THEN** 它们相等且具有相同的哈希值

#### Scenario: 比较不同的清单 — Compare different manifests
- **WHEN** 两个 PluginManifest 实例在 type、name 或 version 上不同
- **THEN** 它们不相等
