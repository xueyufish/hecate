# Spec Delta: skill-api

## ADDED Requirements

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