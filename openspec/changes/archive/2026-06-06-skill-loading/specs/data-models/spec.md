## MODIFIED Requirements — 修改的需求

### Requirement: SkillModel 支持多租户工作空间隔离 — SkillModel 支持多租户工作空间隔离
`SkillModel` SHALL 包含一个 UUID 类型的 `workspace_id` 列，默认为零 UUID `00000000-0000-0000-0000-000000000000`。唯一索引 SHALL 为 `(workspace_id, name)` 而不是仅 `(name)`，允许不同工作空间拥有相同名称的技能。

#### Scenario: 带零 UUID 的系统技能
- **WHEN** 创建 `source="system"` 的技能
- **THEN** `workspace_id` SHALL 默认为 `00000000-0000-0000-0000-000000000000`

#### Scenario: 带工作空间 ID 的用户技能
- **WHEN** 由工作空间 A 的用户创建 `source="user"` 的技能
- **THEN** `workspace_id` SHALL 设置为工作空间 A 的 UUID

#### Scenario: 不同工作空间中的相同技能名称
- **WHEN** 工作空间 A 有一个名为 "helper" 的技能，工作空间 B 创建了一个名为 "helper" 的技能
- **THEN** 两个技能 SHALL 共存，不会违反唯一约束

#### Scenario: SkillCreateSchema 包含 workspace_id
- **WHEN** 通过 API 创建技能
- **THEN** `workspace_id` SHALL 从认证用户的工作空间上下文自动设置，而不是从请求体获取
