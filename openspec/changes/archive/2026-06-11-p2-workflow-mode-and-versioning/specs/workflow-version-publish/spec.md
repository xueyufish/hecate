## ADDED Requirements

### Requirement: Workflow published version pointer
The `WorkflowModel` SHALL include a `published_version` field of type `int | None`, defaulting to `None`. When a version is published, this field SHALL be set to the published version number. Only one version per workflow SHALL be the published version at any time.

#### Scenario: Initial workflow has no published version
- **WHEN** a new workflow is created with version 1
- **THEN** `published_version` SHALL be `None`

#### Scenario: Publish a specific version
- **WHEN** `publish_version(workflow_id, version=3)` is called
- **THEN** `published_version` SHALL be set to `3`
- **AND** an audit log entry with action `WORKFLOW_VERSION_PUBLISH` SHALL be created

#### Scenario: Republish overwrites previous published version
- **WHEN** `published_version` is `3` and `publish_version(workflow_id, version=5)` is called
- **THEN** `published_version` SHALL be updated to `5`
- **AND** the previous published version 3 SHALL remain in the version history unchanged

#### Scenario: Publish non-existent version
- **WHEN** `publish_version(workflow_id, version=99)` is called and version 99 does not exist
- **THEN** the service SHALL raise `ValueError`

### Requirement: Workflow version deployment labels
The `WorkflowVersionModel` SHALL include a `labels` field of type `list[str]`, defaulting to an empty list. Labels SHALL follow the pattern established by `PromptVersionModel.labels`. Standard labels SHALL include `"production"`, `"staging"`, and `"development"`.

#### Scenario: Version created with labels
- **WHEN** a workflow version is created with `labels=["staging"]`
- **THEN** the version SHALL store those labels

#### Scenario: Publish sets production label
- **WHEN** `publish_version(workflow_id, version=3)` is called
- **THEN** version 3 SHALL have `"production"` added to its labels
- **AND** any previously published version SHALL have `"production"` removed from its labels

#### Scenario: Query by label
- **WHEN** `get_version_by_label(workflow_id, label="production")` is called
- **THEN** the service SHALL return the version with `"production"` in its labels

### Requirement: Workflow version diff comparison
The `WorkflowService` SHALL provide a `diff_versions(workflow_id, v1, v2)` method that compares two workflow versions' `graph_dsl` fields and returns a structured diff result. The result SHALL categorize changes into: nodes added, nodes removed, nodes modified, edges added, edges removed, edges modified, state changes.

#### Scenario: Diff between versions with node changes
- **WHEN** `diff_versions(workflow_id, v1=1, v2=2)` is called where version 2 added a "validator" node and removed the "checker" node
- **THEN** the result SHALL contain `{"nodes_added": ["validator"], "nodes_removed": ["checker"], "nodes_modified": [], "edges_added": [], "edges_removed": [], "edges_modified": [], "state_changes": []}`

#### Scenario: Diff between identical versions
- **WHEN** `diff_versions(workflow_id, v1=1, v2=1)` is called
- **THEN** the result SHALL contain empty lists for all change categories

#### Scenario: Diff with non-existent version
- **WHEN** `diff_versions(workflow_id, v1=1, v2=99)` is called and version 99 does not exist
- **THEN** the service SHALL raise `ValueError`

### Requirement: Publish and diff API endpoints
The workflow management API SHALL expose publish and diff endpoints. `POST /api/workflows/{id}/publish/{version}` SHALL publish a specific version. `GET /api/workflows/{id}/diff?v1={v1}&v2={v2}` SHALL return the diff between two versions. `GET /api/workflows/{id}/published` SHALL return the currently published version.

#### Scenario: Publish via API
- **WHEN** `POST /api/workflows/{id}/publish/3` is called
- **THEN** the response SHALL be `{"published_version": 3}` with status 200

#### Scenario: Diff via API
- **WHEN** `GET /api/workflows/{id}/diff?v1=1&v2=2` is called
- **THEN** the response SHALL contain the structured diff result with status 200

#### Scenario: Get published version via API
- **WHEN** `GET /api/workflows/{id}/published` is called and `published_version=3`
- **THEN** the response SHALL return the full version 3 data with status 200

#### Scenario: Get published version when none published
- **WHEN** `GET /api/workflows/{id}/published` is called and `published_version` is `None`
- **THEN** the response SHALL be `{"error": {"code": "NOT_PUBLISHED", "message": "No published version"}}` with status 404
