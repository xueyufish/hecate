## ADDED Requirements — 新增需求

### Requirement: Workflow published version pointer — 工作流已发布版本指针
`WorkflowModel` 应包含一个类型为 `int | None` 的 `published_version` 字段，默认值为 `None`。发布版本时，该字段应设置为已发布的版本号。每个工作流在同一时间只能有一个已发布版本。

#### Scenario: Initial workflow has no published version — 初始工作流无已发布版本
- **当** 新建工作流并创建版本 1
- **则** `published_version` 应为 `None`

#### Scenario: Publish a specific version — 发布特定版本
- **当** 调用 `publish_version(workflow_id, version=3)`
- **则** `published_version` 应设置为 `3`
- **并且** 应创建一条 action 为 `WORKFLOW_VERSION_PUBLISH` 的审计日志条目

#### Scenario: Republish overwrites previous published version — 重新发布覆盖之前的已发布版本
- **当** `published_version` 为 `3` 且调用 `publish_version(workflow_id, version=5)`
- **则** `published_version` 应更新为 `5`
- **并且** 之前发布的版本 3 应在版本历史中保持不变

#### Scenario: Publish non-existent version — 发布不存在的版本
- **当** 调用 `publish_version(workflow_id, version=99)` 但版本 99 不存在
- **则** 服务应抛出 `ValueError`

### Requirement: Workflow version deployment labels — 工作流版本部署标签
`WorkflowVersionModel` 应包含一个类型为 `list[str]` 的 `labels` 字段，默认为空列表。标签应遵循 `PromptVersionModel.labels` 建立的模式。标准标签应包括 `"production"`、`"staging"` 和 `"development"`。

#### Scenario: Version created with labels — 创建版本时带标签
- **当** 使用 `labels=["staging"]` 创建工作流版本
- **则** 版本应存储这些标签

#### Scenario: Publish sets production label — 发布设置生产标签
- **当** 调用 `publish_version(workflow_id, version=3)`
- **则** 版本 3 应将 `"production"` 添加到其标签中
- **并且** 任何先前发布的版本应从其标签中移除 `"production"`

#### Scenario: Query by label — 按标签查询
- **当** 调用 `get_version_by_label(workflow_id, label="production")`
- **则** 服务应返回标签中包含 `"production"` 的版本

### Requirement: Workflow version diff comparison — 工作流版本差异比较
`WorkflowService` 应提供一个 `diff_versions(workflow_id, v1, v2)` 方法，用于比较两个工作流版本的 `graph_dsl` 字段，并返回结构化的差异结果。结果应将变更分类为：节点新增、节点删除、节点修改、边新增、边删除、边修改、状态变更。

#### Scenario: Diff between versions with node changes — 包含节点变更的版本间差异
- **当** 调用 `diff_versions(workflow_id, v1=1, v2=2)`，其中版本 2 新增了 "validator" 节点并删除了 "checker" 节点
- **则** 结果应包含 `{"nodes_added": ["validator"], "nodes_removed": ["checker"], "nodes_modified": [], "edges_added": [], "edges_removed": [], "edges_modified": [], "state_changes": []}`

#### Scenario: Diff between identical versions — 相同版本间的差异
- **当** 调用 `diff_versions(workflow_id, v1=1, v2=1)`
- **则** 结果应包含所有变更类别的空列表

#### Scenario: Diff with non-existent version — 与不存在的版本进行差异比较
- **当** 调用 `diff_versions(workflow_id, v1=1, v2=99)` 但版本 99 不存在
- **则** 服务应抛出 `ValueError`

### Requirement: Publish and diff API endpoints — 发布和差异 API 端点
工作流管理 API 应暴露发布和差异端点。`POST /api/workflows/{id}/publish/{version}` 应发布特定版本。`GET /api/workflows/{id}/diff?v1={v1}&v2={v2}` 应返回两个版本之间的差异。`GET /api/workflows/{id}/published` 应返回当前已发布的版本。

#### Scenario: Publish via API — 通过 API 发布
- **当** 调用 `POST /api/workflows/{id}/publish/3`
- **则** 响应应为 `{"published_version": 3}`，状态码 200

#### Scenario: Diff via API — 通过 API 获取差异
- **当** 调用 `GET /api/workflows/{id}/diff?v1=1&v2=2`
- **则** 响应应包含结构化的差异结果，状态码 200

#### Scenario: Get published version via API — 通过 API 获取已发布版本
- **当** 调用 `GET /api/workflows/{id}/published` 且 `published_version=3`
- **则** 响应应返回完整的版本 3 数据，状态码 200

#### Scenario: Get published version when none published — 获取未发布版本的已发布版本
- **当** 调用 `GET /api/workflows/{id}/published` 且 `published_version` 为 `None`
- **则** 响应应为 `{"error": {"code": "NOT_PUBLISHED", "message": "No published version"}}`，状态码 404
