# skill-api Specification

## Purpose
Provides the workspace-facing skill CRUD and SKILL.md import API, including provider-registry metadata exposure and invocation-policy validation.

## Requirements

### Requirement: Create skill via API
The system SHALL provide a `POST /api/skills` endpoint that accepts a JSON body with name, description, source, instructions, and optional fields, creates a `SkillModel` record, and returns the created skill. The `SkillModel` SHALL support the source values `system`, `user`, `project`, and `plugin`, and SHALL carry nullable provenance fields `origin` (string) and `plugin_id` (UUID, set only for `source="plugin"` rows). This endpoint SHALL accept only the user-facing values (`system`, `user`, `project`); `plugin` is reserved for the ingestion pipeline. The endpoint SHALL also accept optional invocation-policy flags `model_invocable` and `user_invocable` (both defaulting to `true`), and SHALL reject the combination `auto_load=true` with `model_invocable=false` as a validation error. The `trust_tier` is not client-settable on this endpoint: user- and project-origin skills are created with `community`, and the response SHALL include `provider`, `trust_tier`, `model_invocable`, `user_invocable`, and `content_hash`. A create request whose `name` and resolved `provider` collide with an existing skill in the same workspace SHALL return 409 Conflict; same-name skills with a different `provider` SHALL be allowed to coexist.

#### Scenario: Create skill with all fields
- **WHEN** `POST /api/skills` is called with `{"name": "code-review", "description": "...", "source": "user", "instructions": "..."}`
- **THEN** a new `SkillModel` SHALL be created with `workspace_id` from the authenticated user's workspace, `trust_tier="community"`, `content_hash` computed over the content fields, and the API SHALL return 201 with the full skill data including the registry metadata fields

#### Scenario: Duplicate name in same workspace and same provider
- **WHEN** `POST /api/skills` is called with a name and provider matching an existing skill in the same workspace
- **THEN** the API SHALL return 409 Conflict

#### Scenario: Same name with different provider accepted
- **WHEN** `POST /api/skills` is called with `source="user"` and a name that already exists in the workspace only as a `project` skill
- **THEN** the API SHALL return 201 and the two skills SHALL coexist

#### Scenario: Invalid source value
- **WHEN** `POST /api/skills` is called with `source="invalid"`
- **THEN** the API SHALL return 422 Validation Error

#### Scenario: Plugin source rejected on manual create
- **WHEN** `POST /api/skills` is called with `source="plugin"`
- **THEN** the API SHALL return 422 Validation Error indicating `plugin` is reserved for package ingestion

#### Scenario: Auto-load with hidden model invocation rejected
- **WHEN** `POST /api/skills` is called with `{"auto_load": true, "model_invocable": false}`
- **THEN** the API SHALL return 422 Validation Error explaining the conflict

#### Scenario: Invocation policy flags default to true
- **WHEN** `POST /api/skills` is called without `model_invocable` or `user_invocable`
- **THEN** the created skill SHALL have both flags set to `true`


### Requirement: Update skill via API
The system SHALL provide a `PUT /api/skills/{id}` endpoint that accepts a JSON body with optional fields to update, modifies the `SkillModel` record, and returns the updated skill.

#### Scenario: Update skill description
- **WHEN** `PUT /api/skills/{id}` is called with `{"description": "Updated description"}`
- **THEN** the skill's description SHALL be updated and the API SHALL return 200 with full skill data

#### Scenario: Update non-existent skill
- **WHEN** `PUT /api/skills/{id}` is called with a non-existent ID
- **THEN** the API SHALL return 404 Not Found


### Requirement: Delete skill via API
The system SHALL provide a `DELETE /api/skills/{id}` endpoint that soft-deletes the `SkillModel` record (sets `deleted_at` timestamp).

#### Scenario: Delete existing skill
- **WHEN** `DELETE /api/skills/{id}` is called for an existing skill
- **THEN** the skill SHALL be soft-deleted (deleted_at set) and the API SHALL return 200

#### Scenario: Delete non-existent skill
- **WHEN** `DELETE /api/skills/{id}` is called with a non-existent ID
- **THEN** the API SHALL return 404 Not Found


### Requirement: Import skill from SKILL.md file
The system SHALL provide a `POST /api/skills/import` endpoint that accepts a SKILL.md file (YAML frontmatter + Markdown body), parses it, and creates a `SkillModel` record with `source="user"`, a `content_hash` computed over the same content field set used by agent-version reference manifests (name, instructions, allowed tools, scripts, references), and default invocation-policy flags. A same-name skill of a different `provider` in the workspace SHALL NOT block the import; a same-name `user`-provider skill SHALL be rejected with 409 Conflict.

#### Scenario: Import valid SKILL.md
- **WHEN** `POST /api/skills/import` is called with a file containing valid YAML frontmatter (name, description) and Markdown body
- **THEN** the system SHALL parse the frontmatter into model fields, use the Markdown body as `instructions`, create a `SkillModel` with `source="user"`, `trust_tier="community"`, and a populated `content_hash`, and return 201 with the created skill

#### Scenario: Import SKILL.md missing required frontmatter
- **WHEN** `POST /api/skills/import` is called with a file missing the `name` field in frontmatter
- **THEN** the API SHALL return 422 with error indicating the missing required field

#### Scenario: Import SKILL.md with no frontmatter
- **WHEN** `POST /api/skills/import` is called with a plain Markdown file (no `---` delimiters)
- **THEN** the API SHALL return 422 with error indicating invalid SKILL.md format

#### Scenario: Import same name as project skill allowed
- **WHEN** the workspace already contains a `project` skill with the same name as the imported file
- **THEN** the import SHALL succeed and the imported `user` skill SHALL coexist with the `project` skill

#### Scenario: Import same name and provider rejected
- **WHEN** the workspace already contains a `user`-provider skill with the same name as the imported file
- **THEN** the API SHALL return 409 Conflict


### Requirement: Manage agent-skill associations
The system SHALL provide endpoints to add and remove skill associations from an agent.

#### Scenario: Add skill to agent
- **WHEN** `POST /api/agents/{id}/skills` is called with `{"skill_name": "code-review"}`
- **THEN** the skill name SHALL be appended to the agent's `skills` list if not already present, and the API SHALL return 200 with the updated skills list

#### Scenario: Add duplicate skill to agent
- **WHEN** `POST /api/agents/{id}/skills` is called with a skill name already in the agent's `skills` list
- **THEN** the API SHALL return 200 with the unchanged skills list (idempotent)

#### Scenario: Remove skill from agent
- **WHEN** `DELETE /api/agents/{id}/skills/{skill_name}` is called
- **THEN** the skill name SHALL be removed from the agent's `skills` list and the API SHALL return 200

#### Scenario: Remove non-existent skill from agent
- **WHEN** `DELETE /api/agents/{id}/skills/{skill_name}` is called with a skill name not in the agent's `skills` list
- **THEN** the API SHALL return 200 with the unchanged skills list (idempotent)





### Requirement: Plugin-derived skills are lifecycle-managed
Skills with `source="plugin"` SHALL be readable through the skill list and detail endpoints with their provenance fields (`origin`, `plugin_id`) visible. Update and delete operations on a plugin-derived skill via the skill API SHALL be rejected with 409 Conflict directing the caller to the owning plugin's lifecycle (enable/disable/uninstall); these rows are managed exclusively by the ingestion pipeline.

#### Scenario: List includes plugin-derived skills
- **WHEN** `GET /api/skills` is called in a workspace with an installed agent-plugin package
- **THEN** the imported skills appear with `source="plugin"` and their `origin` and `plugin_id` populated

#### Scenario: Update plugin-derived skill rejected
- **WHEN** `PUT /api/skills/{id}` is called for a skill with `source="plugin"`
- **THEN** the API SHALL return 409 Conflict without modifying the skill

#### Scenario: Delete plugin-derived skill rejected
- **WHEN** `DELETE /api/skills/{id}` is called for a skill with `source="plugin"`
- **THEN** the API SHALL return 409 Conflict without deleting the skill


### Requirement: Skill API exposes provider registry metadata
The skill list and detail endpoints SHALL include, for every skill, its `provider`, `trust_tier`, `model_invocable`, `user_invocable`, and `content_hash`. The `trust_tier` and `provider` SHALL be read-only on workspace-scoped update paths: an update request attempting to change either SHALL be rejected with a validation error. Workspace-scoped update requests MAY change the invocation-policy flags, subject to the auto-load consistency rule.

#### Scenario: List includes registry metadata
- **WHEN** `GET /api/skills` is called in a workspace
- **THEN** each returned skill SHALL include `provider`, `trust_tier`, `model_invocable`, `user_invocable`, and `content_hash`

#### Scenario: Update invocation policy
- **WHEN** `PUT /api/skills/{id}` is called with `{"model_invocable": false}` on a skill with `auto_load=false`
- **THEN** the skill's `model_invocable` SHALL become `false` and the API SHALL return 200 with the updated skill

#### Scenario: Trust tier not updatable via workspace API
- **WHEN** `PUT /api/skills/{id}` is called with `{"trust_tier": "official"}`
- **THEN** the API SHALL return 422 Validation Error without modifying the skill

### Requirement: 创建 skill 接受 requires 字段

`POST /api/skills` SHALL 接受可选 `requires` 字段(数组,元素形如 `{name, provider?}`)。系统 SHALL:

- 在创建前对 `requires` 的传递闭包执行图校验(见 `skill-dependency-declaration` 的缺失依赖 / 环检测 / 跨 source 拒绝)
- 校验失败时返回 422 Validation Error,响应体包含缺漏依赖名或环路径
- 校验通过后将 `requires` 镜像写入 `SkillModel.requires` 列(JSON)
- 在响应体中返回 `requires` 字段

#### Scenario: 创建 skill 带 requires 成功
- **WHEN** `POST /api/skills` 传入 `{"name": "pdf-report", ..., "requires": [{"name": "pdf-utils"}]}` 且 pdf-utils 存在
- **THEN** API 返回 201,响应包含 `requires: [{name: "pdf-utils"}]`

#### Scenario: 创建 skill 带缺漏 requires 被拒
- **WHEN** `POST /api/skills` 传入 `{"name": "pdf-report", "requires": [{"name": "missing-skill"}]}`
- **THEN** API 返回 422,错误信息包含「missing-skill」

### Requirement: 更新 skill 校验 requires 字段

`PUT /api/skills/{id}` SHALL 接受可选 `requires` 字段;若提供,系统 SHALL 执行与创建一致的图校验。校验失败时返回 422。不提供 `requires` 时 SHALL NOT 修改现有 `requires` 列。

#### Scenario: 更新 requires 成功
- **WHEN** `PUT /api/skills/{id}` 传入 `{"requires": [{"name": "new-dep"}]}`,new-dep 存在
- **THEN** API 返回 200,`SkillModel.requires` 被更新为 `[{name: "new-dep"}]`

#### Scenario: 更新 requires 引入环被拒
- **WHEN** `PUT /api/skills/{id}` 传入 `{"requires": [{"name": "self-ref"}]}` 且 self-ref 的 `requires` 含本 skill
- **THEN** API 返回 422,错误信息说明环位置

### Requirement: 导入 SKILL.md 解析 requires frontmatter

`POST /api/skills/import` SHALL 解析 SKILL.md frontmatter 中的 `requires` 字段,并在创建 SkillModel 前对 `requires` 传递闭包执行图校验。校验失败时返回 422。

#### Scenario: 导入 SKILL.md with requires 成功
- **WHEN** `POST /api/skills/import` 上传的 SKILL.md frontmatter 含 `requires: [{name: "pdf-utils"}]`,pdf-utils 存在
- **THEN** API 返回 201,`SkillModel.requires` 存为 `[{name: "pdf-utils"}]`

#### Scenario: 导入 SKILL.md 缺漏 requires 被拒
- **WHEN** 上传 SKILL.md frontmatter 含 `requires: [{name: "missing"}]`,missing 不存在
- **THEN** API 返回 422,错误信息包含「missing」,不创建 SkillModel

### Requirement: 422 错误包含结构化路径信息

`requires` 校验失败的 422 响应 SHALL 在响应体中包含机器可读字段:

- `error_code`:枚举 `dependency_missing` / `dependency_cycle` / `cross_source_denied` / `invalid_requires_syntax` / `self_reference`
- `dependency_path`:从当前 skill 出发的依赖链(环检测场景)

#### Scenario: 422 响应含 error_code
- **WHEN** 缺漏依赖触发 422
- **THEN** 响应体 `error_code = "dependency_missing"`,`dependency_path` 数组包含完整传递链

#### Scenario: 422 响应含环路径
- **WHEN** 环检测触发 422
- **THEN** 响应体 `error_code = "dependency_cycle"`,`dependency_path` 数组按环遍历顺序记录节点
