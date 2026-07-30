## ADDED Requirements — 新增需求

### Requirement: Create workflow — 创建工作流
`POST /api/workflows` SHALL accept a name and optional graph_dsl, create a WorkflowModel, compile the DSL into a WorkflowVersionModel (version 1), and return the workflow with its first version.

`POST /api/workflows` 应接受名称和可选的 graph_dsl，创建 WorkflowModel，将 DSL 编译为 WorkflowVersionModel（版本 1），并返回工作流及其第一个版本。

#### Scenario: Create empty workflow — 创建空工作流
- **WHEN** POST /api/workflows with `{"name": "My Flow"}`
- **THEN** response is 201 with workflow id, name, current_version=1, and an empty graph_dsl version
- **当** POST /api/workflows，参数为 `{"name": "My Flow"}`
- **则**响应为 201，包含 workflow id、name、current_version=1 以及空的 graph_dsl 版本

#### Scenario: Create workflow with initial DSL — 创建带初始 DSL 的工作流
- **WHEN** POST /api/workflows with `{"name": "My Flow", "graph_dsl": {...}}`
- **THEN** response is 201 with the DSL compiled and stored as version 1
- **当** POST /api/workflows，参数为 `{"name": "My Flow", "graph_dsl": {...}}`
- **则**响应为 201，DSL 被编译并存储为版本 1

### Requirement: List workflows — 列出工作流
`GET /api/workflows` SHALL return a paginated list of workflows with name, current_version, created_at, updated_at.

`GET /api/workflows` 应返回分页的工作流列表，包含 name、current_version、created_at、updated_at。

#### Scenario: List workflows with pagination — 分页列出工作流
- **WHEN** GET /api/workflows?page=1&page_size=20
- **THEN** response is 200 with `{"items": [...], "total": int}`
- **当** GET /api/workflows?page=1&page_size=20
- **则**响应为 200，包含 `{"items": [...], "total": int}`

### Requirement: Get workflow with current version — 获取工作流及其当前版本
`GET /api/workflows/{id}` SHALL return the workflow metadata plus the current version's graph_dsl and compiled_graph.

`GET /api/workflows/{id}` 应返回工作流元数据以及当前版本的 graph_dsl 和 compiled_graph。

#### Scenario: Get existing workflow — 获取已存在的工作流
- **WHEN** GET /api/workflows/{id} for an existing workflow
- **THEN** response is 200 with workflow fields plus `version` containing graph_dsl and compiled_graph
- **当** GET /api/workflows/{id}，目标为已存在的工作流
- **则**响应为 200，包含工作流字段以及包含 graph_dsl 和 compiled_graph 的 `version` 字段

#### Scenario: Get non-existent workflow — 获取不存在的工作流
- **WHEN** GET /api/workflows/{id} for a non-existent ID
- **THEN** response is 404
- **当** GET /api/workflows/{id}，目标为不存在的 ID
- **则**响应为 404

### Requirement: Update workflow creates new version — 更新工作流创建新版本
`PUT /api/workflows/{id}` SHALL accept name and/or graph_dsl changes, increment current_version, compile the new DSL, and store a new WorkflowVersionModel. Previous versions SHALL remain immutable.

`PUT /api/workflows/{id}` 应接受名称和/或 graph_dsl 更改，递增 current_version，编译新的 DSL，并存储新的 WorkflowVersionModel。先前版本应保持不可变。

#### Scenario: Update workflow DSL — 更新工作流 DSL
- **WHEN** PUT /api/workflows/{id} with `{"graph_dsl": {...}, "change_summary": "added condition node"}`
- **THEN** response is 200 with current_version incremented and a new version created
- **当** PUT /api/workflows/{id}，参数为 `{"graph_dsl": {...}, "change_summary": "added condition node"}`
- **则**响应为 200，current_version 递增并创建新版本

#### Scenario: Update workflow name only — 仅更新工作流名称
- **WHEN** PUT /api/workflows/{id} with `{"name": "New Name"}`
- **THEN** name is updated but no new version is created (DSL unchanged)
- **当** PUT /api/workflows/{id}，参数为 `{"name": "New Name"}`
- **则**名称被更新但不会创建新版本（DSL 不变）

### Requirement: Delete workflow — 删除工作流
`DELETE /api/workflows/{id}` SHALL soft-delete the workflow (set deleted_at). Versions remain for audit.

`DELETE /api/workflows/{id}` 应软删除工作流（设置 deleted_at）。版本将保留以供审计。

#### Scenario: Delete existing workflow — 删除已存在的工作流
- **WHEN** DELETE /api/workflows/{id}
- **THEN** response is 204 and subsequent GET returns 404
- **当** DELETE /api/workflows/{id}
- **则**响应为 204，后续 GET 返回 404

### Requirement: Validate workflow DSL — 验证工作流 DSL
`POST /api/workflows/{id}/validate` SHALL run the DSL through the compiler (dry-run) without executing and return validation errors or success.

`POST /api/workflows/{id}/validate` 应将 DSL 通过编译器运行（预演模式）而不实际执行，并返回验证错误或成功。

#### Scenario: Validate valid DSL — 验证有效的 DSL
- **WHEN** POST /api/workflows/{id}/validate with valid graph_dsl
- **THEN** response is 200 with `{"valid": true}`
- **当** POST /api/workflows/{id}/validate，使用有效的 graph_dsl
- **则**响应为 200，包含 `{"valid": true}`

#### Scenario: Validate invalid DSL — 验证无效的 DSL
- **WHEN** POST /api/workflows/{id}/validate with graph_dsl missing required edges
- **THEN** response is 200 with `{"valid": false, "errors": ["..."]}`
- **当** POST /api/workflows/{id}/validate，使用缺少必需边的 graph_dsl
- **则**响应为 200，包含 `{"valid": false, "errors": ["..."]}`

### Requirement: Get workflow version history — 获取工作流版本历史
`GET /api/workflows/{id}/versions` SHALL return all versions ordered by version number descending.

`GET /api/workflows/{id}/versions` 应返回所有版本，按版本号降序排列。

#### Scenario: List versions — 列出版本
- **WHEN** GET /api/workflows/{id}/versions
- **THEN** response is 200 with array of versions including change_summary and created_at
- **当** GET /api/workflows/{id}/versions
- **则**响应为 200，包含版本数组，每个版本包括 change_summary 和 created_at
