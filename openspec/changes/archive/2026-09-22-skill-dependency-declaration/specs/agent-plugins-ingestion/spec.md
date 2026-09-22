# Spec Delta: agent-plugins-ingestion

## ADDED Requirements

### Requirement: plugin.json 通过 Hecate namespace 接受 requires

Agent Plugins 1.0 `plugin.json` SHALL 通过 `extensions.io.github.xueyufish` namespace 接受 `requires` 数组(沿用既有 Hecate namespace 扩展机制,与 `type` / `entry` / `permissions` / `config_schema` 等 Hecate 私有字段并列)。

plugin-level `requires` 在该包内所有 skill 间共享,适用与 skill 级 `requires` 相同的图校验规则(缺失依赖 / 环 / 跨 source 拒绝)。

plugin.json 验证器 SHALL:

- 接受 `extensions.io.github.xueyufish.requires` 数组
- 沿用既有 closed-manifest 验证:未知顶层字段 warn + 忽略,plugin-level `requires` **不**出现在顶层
- 在插件导入时执行图校验;校验失败则整个插件安装拒绝

#### Scenario: Hecate namespace requires 导入成功
- **WHEN** plugin.json 含 `extensions.io.github.xueyufish.requires: [{name: "helper-utils"}]`,helper-utils 存在
- **THEN** 插件安装成功,`PluginModel.manifest_json` 持久化 requires 数组

#### Scenario: namespace requires 缺漏被拒
- **WHEN** plugin.json 含 `extensions.io.github.xueyufish.requires: [{name: "missing-helper"}]`,helper 不存在
- **THEN** 整个插件安装被拒,错误信息包含 missing-helper,不写入 PluginModel

#### Scenario: 顶层 requires 字段被 warn
- **WHEN** plugin.json 顶层含 `requires: [...]`(未在 namespace 内)
- **THEN** 验证器 warn 该字段被忽略;plugin-level require 不生效;skill 级 `requires`(SKILL.md frontmatter 内)按既有规则处理

### Requirement: plugin.json requires 与 SKILL.md frontmatter requires 合并求值

plugin 内每个 skill 的闭包 SHALL 是「SKILL.md frontmatter requires + plugin.json namespace requires」的并集。两者出现同名依赖时,按 plugin.json namespace 优先(namespace 是 plugin 范围约束,frontmatter 是单 skill 约束)。

#### Scenario: namespace + frontmatter 合并为并集
- **WHEN** plugin.json namespace requires = [A],包内 skill S 的 SKILL.md requires = [B]
- **THEN** S 的隐式闭包包含 A 和 B

#### Scenario: 同一依赖重复声明去重
- **WHEN** plugin.json namespace requires = [A],包内 skill S 的 SKILL.md requires = [A]
- **THEN** S 的闭包包含 A(单条),plugin.json namespace 优先但去重后行为等价