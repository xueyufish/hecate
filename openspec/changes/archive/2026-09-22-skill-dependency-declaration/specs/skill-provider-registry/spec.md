# Spec Delta: skill-provider-registry

## ADDED Requirements

### Requirement: requires 跨 source 默认拒绝

skill 的 `requires` 字段 SHALL 默认拒绝跨 source 引用(详见 `skill-dependency-declaration` 的跨 source 拒绝规则)。允许矩阵:

- `user` skill 可 require `bundled` 或同 source 的 `user` skill
- `project` skill 可 require `bundled` / `user` / `project`
- `bundled` skill 可 require 同 workspace 的 `bundled` skill
- **任何非 plugin source 不可 require plugin source skill**
- plugin source skill 的 require 通过 `plugin.json` namespace 扩展处理,不在 SKILL.md frontmatter 层

#### Scenario: user skill require project skill 被拒
- **WHEN** 创建 `user` skill,`requires: [{name: "P", provider: "project"}]`
- **THEN** 请求被拒,错误信息说明跨 source 不兼容

#### Scenario: 非 plugin require plugin source 被拒
- **WHEN** skill 的 `requires: [{name: "plugin-skill", provider: "plugin"}]`
- **THEN** 请求被拒

### Requirement: trust tier 不沿 requires 边升级

skill 的 trust tier SHALL NOT 沿 `requires` 边升级。闭包中每个节点维持自身 trust tier,沿用既有 anti-escalation 语义(社区 / trusted / official 不通过 require 关系相互抬升)。

#### Scenario: community requires official 维持 community
- **WHEN** skill C(trust_tier=community) requires skill O(trust_tier=official)
- **THEN** C 维持 community,O 维持 official;绑定期闭包中两者 tier 不变

#### Scenario: official skill requires community 不下沉 official
- **WHEN** skill O(trust_tier=official) requires skill C(trust_tier=community)
- **THEN** O 维持 official,C 维持 community;trust 反向下沉也禁止(沿用 anti-escalation 单向规则)