## ADDED Requirements — 新增需求

### Requirement: ModelDeploymentModel ORM 模型 — ModelDeploymentModel ORM model
系统应在 `models/model_deployment.py` 中定义 `ModelDeploymentModel(BaseModel)`，包含字段：`model_id`（String 255，提供者模型名称）、`channel`（String 20，取值：dev、staging、prod）、`version`（String 50，可空）、`deployment_config`（JSON，此部署的覆盖）、`approval_status`（String 20，取值：pending、approved、rejected，默认 pending）、`approved_by`（UUID，可空）、`approved_at`（DateTime，可空）、`deprecated_at`（DateTime，可空）、`sunset_at`（DateTime，可空）、`workspace_id`（UUID）。

#### Scenario: 创建部署 — Create deployment
- **WHEN** 使用 `model_id="gpt-4o"`、`channel="dev"`、`approval_status="pending"` 创建 ModelDeploymentModel
- **THEN** 记录以指定通道和待审批状态持久化

#### Scenario: 每个通道唯一模型 — Unique model per channel
- **WHEN** 为已存在的 model_id + channel 组合创建部署
- **THEN** 系统应拒绝重复并返回错误

### Requirement: 模型提升工作流 — Model promotion workflow
系统应暴露 `/api/models/{model_id}/promote` 端点，将模型从一个通道移动到下一个通道（dev → staging → prod）。

#### Scenario: 从 dev 提升到 staging — Promote from dev to staging
- **WHEN** 收到 POST `/api/models/gpt-4o/promote`，带 `{"from": "dev", "to": "staging"}`
- **THEN** 系统应创建新的 ModelDeploymentModel，`channel="staging"`、`approval_status="pending"`

#### Scenario: 审批提升 — Approve promotion
- **WHEN** 工作区管理员收到 POST `/api/models/gpt-4o/promote/{deployment_id}/approve`
- **THEN** 系统应设置 `approval_status="approved"`、`approved_by=<user_id>`、`approved_at=now`

#### Scenario: 拒绝提升 — Reject promotion
- **WHEN** 收到带原因的 POST `/api/models/gpt-4o/promote/{deployment_id}/reject`
- **THEN** 系统应设置 `approval_status="rejected"` 并保持之前的通道活动

#### Scenario: 无审批的提升 — Promotion without approval
- **WHEN** 待审批的部署在批准前被使用
- **THEN** 系统应拒绝该部署的模型调用，错误为 "Deployment pending approval"

### Requirement: 模型弃用调度 — Model deprecation scheduling
系统应暴露 `/api/models/{model_id}/deprecate` 端点，用于调度模型弃用并设置日落日期。

#### Scenario: 调度弃用 — Schedule deprecation
- **WHEN** 收到 POST `/api/models/gpt-4o/deprecate`，带 `{"sunset_at": "2026-08-01T00:00:00Z"}`
- **THEN** 系统应在 prod 部署上设置 `deprecated_at=now` 和 `sunset_at`

#### Scenario: 30 天时的日落通知 — Sunset notification at 30 days
- **WHEN** 日落日期还有 30 天
- **THEN** 系统应触发 AlertService 通知 "Model gpt-4o sunsetting in 30 days"

#### Scenario: 日落时自动禁用 — Automatic disable at sunset
- **WHEN** 当前时间超过 `sunset_at`
- **THEN** 系统应设置部署 `is_enabled=False` 并拒绝进一步的调用

#### Scenario: 取消弃用 — Cancel deprecation
- **WHEN** 收到 POST `/api/models/gpt-4o/deprecate/cancel`
- **THEN** 系统应清除 `deprecated_at` 和 `sunset_at`，恢复正常操作

### Requirement: 部署列表 API — Deployment listing API
系统应暴露 `/api/models/deployments` 端点，用于列出所有模型部署及其通道和审批状态。

#### Scenario: 列出所有部署 — List all deployments
- **WHEN** 收到 GET `/api/models/deployments`
- **THEN** 系统应返回所有部署，包含 model_id、channel、approval_status、version、deprecated_at、sunset_at

#### Scenario: 按通道过滤 — Filter by channel
- **WHEN** 收到 GET `/api/models/deployments?channel=prod`
- **THEN** 系统应仅返回 prod 通道中的部署

#### Scenario: 按审批状态过滤 — Filter by approval status
- **WHEN** 收到 GET `/api/models/deployments?approval_status=pending`
- **THEN** 系统应仅返回等待审批的待审批部署

### Requirement: 模型回滚 — Model rollback
系统应暴露 `/api/models/{model_id}/rollback` 端点，用于将模型恢复到之前的部署版本。

#### Scenario: 回滚到之前版本 — Rollback to previous version
- **WHEN** 收到 POST `/api/models/gpt-4o/rollback`，带 `{"to_version": "v1.0"}`
- **THEN** 系统应创建指向之前版本的新部署，并将当前部署标记为 rolled_back

#### Scenario: 回滚创建审计跟踪 — Rollback creates audit trail
- **WHEN** 执行回滚
- **THEN** 系统应在部署历史中记录谁发起了回滚、时间和原因
