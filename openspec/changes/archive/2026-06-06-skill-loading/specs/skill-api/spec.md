## ADDED Requirements — 新增需求

### Requirement: 通过 API 创建技能 — 通过 API 创建技能
系统 SHALL 提供一个 `POST /api/skills` 端点，接受包含 name、description、source、instructions 和可选字段的 JSON 正文，创建 `SkillModel` 记录，并返回创建的技能。

#### Scenario: 使用所有字段创建技能
- **WHEN** 使用 `{"name": "code-review", "description": "...", "source": "user", "instructions": "..."}` 调用 `POST /api/skills`
- **THEN** 一个新的 `SkillModel` SHALL 被创建，`workspace_id` 来自认证用户的工作空间，API SHALL 返回 201 及完整的技能数据

#### Scenario: 同一工作空间中的重复名称
- **WHEN** 使用一个已存在于同一工作空间中的名称调用 `POST /api/skills`
- **THEN** API SHALL 返回 409 Conflict

#### Scenario: 无效的 source 值
- **WHEN** 使用 `source="invalid"` 调用 `POST /api/skills`
- **THEN** API SHALL 返回 422 Validation Error

### Requirement: 通过 API 更新技能 — 通过 API 更新技能
系统 SHALL 提供一个 `PUT /api/skills/{id}` 端点，接受包含可选更新字段的 JSON 正文，修改 `SkillModel` 记录，并返回更新后的技能。

#### Scenario: 更新技能描述
- **WHEN** 使用 `{"description": "Updated description"}` 调用 `PUT /api/skills/{id}`
- **THEN** 技能的描述 SHALL 被更新，API SHALL 返回 200 及完整技能数据

#### Scenario: 更新不存在的技能
- **WHEN** 使用不存在的 ID 调用 `PUT /api/skills/{id}`
- **THEN** API SHALL 返回 404 Not Found

### Requirement: 通过 API 删除技能 — 通过 API 删除技能
系统 SHALL 提供一个 `DELETE /api/skills/{id}` 端点，软删除 `SkillModel` 记录（设置 `deleted_at` 时间戳）。

#### Scenario: 删除现有技能
- **WHEN** 为现有技能调用 `DELETE /api/skills/{id}`
- **THEN** 技能 SHALL 被软删除（设置 deleted_at），API SHALL 返回 200

#### Scenario: 删除不存在的技能
- **WHEN** 使用不存在的 ID 调用 `DELETE /api/skills/{id}`
- **THEN** API SHALL 返回 404 Not Found

### Requirement: 从 SKILL.md 文件导入技能 — 从 SKILL.md 文件导入技能
系统 SHALL 提供一个 `POST /api/skills/import` 端点，接受 SKILL.md 文件（YAML 前置元数据 + Markdown 正文），解析它，并创建 `SkillModel` 记录。

#### Scenario: 导入有效的 SKILL.md
- **WHEN** 使用包含有效 YAML 前置元数据（name、description）和 Markdown 正文的文件调用 `POST /api/skills/import`
- **THEN** 系统 SHALL 将前置元数据解析到模型字段，使用 Markdown 正文作为 `instructions`，创建 `source="user"` 的 `SkillModel`，并返回 201 及创建的技能

#### Scenario: 导入缺少必需前置元数据的 SKILL.md
- **WHEN** 使用前置元数据中缺少 `name` 字段的文件调用 `POST /api/skills/import`
- **THEN** API SHALL 返回 422，并提示缺少必填字段

#### Scenario: 导入没有前置元数据的 SKILL.md
- **WHEN** 使用纯 Markdown 文件（没有 `---` 分隔符）调用 `POST /api/skills/import`
- **THEN** API SHALL 返回 422，并提示无效的 SKILL.md 格式

### Requirement: 管理 agent-技能关联 — 管理 agent-技能关联
系统 SHALL 提供端点来添加和移除 agent 的技能关联。

#### Scenario: 向 agent 添加技能
- **WHEN** 使用 `{"skill_name": "code-review"}` 调用 `POST /api/agents/{id}/skills`
- **THEN** 技能名称 SHALL 被追加到 agent 的 `skills` 列表（如果尚未存在），API SHALL 返回 200 及更新后的技能列表

#### Scenario: 向 agent 添加重复技能
- **WHEN** 使用一个已存在于 agent 的 `skills` 列表中的技能名称调用 `POST /api/agents/{id}/skills`
- **THEN** API SHALL 返回 200 及未更改的技能列表（幂等）

#### Scenario: 从 agent 移除技能
- **WHEN** 调用 `DELETE /api/agents/{id}/skills/{skill_name}`
- **THEN** 技能名称 SHALL 从 agent 的 `skills` 列表中移除，API SHALL 返回 200

#### Scenario: 从 agent 移除不存在的技能
- **WHEN** 使用一个不在 agent 的 `skills` 列表中的技能名称调用 `DELETE /api/agents/{id}/skills/{skill_name}`
- **THEN** API SHALL 返回 200 及未更改的技能列表（幂等）

### Requirement: 按工作空间过滤列出技能 — 按工作空间过滤列出技能
现有的 `GET /api/skills` 端点 SHALL 按认证用户的工作空间 ID 过滤结果，不返回其他工作空间的技能。

#### Scenario: 列出技能仅返回工作空间技能
- **WHEN** 工作空间 A 的用户调用 `GET /api/skills`
- **THEN** 仅 `workspace_id=A` 的技能 SHALL 被返回，排除其他工作空间的技能

#### Scenario: 系统技能对所有工作空间可见
- **WHEN** 任何用户调用 `GET /api/skills`
- **THEN** `workspace_id=00000000`（系统技能）的技能 SHALL 也包含在结果中
