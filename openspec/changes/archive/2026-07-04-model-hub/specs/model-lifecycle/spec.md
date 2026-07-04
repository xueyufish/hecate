## ADDED Requirements

### Requirement: ModelDeploymentModel ORM model
The system SHALL define `ModelDeploymentModel(BaseModel)` in `models/model_deployment.py` with fields: `model_id` (String 255, the provider model name), `channel` (String 20, one of: dev, staging, prod), `version` (String 50, nullable), `deployment_config` (JSON, overrides for this deployment), `approval_status` (String 20, one of: pending, approved, rejected, default pending), `approved_by` (UUID, nullable), `approved_at` (DateTime, nullable), `deprecated_at` (DateTime, nullable), `sunset_at` (DateTime, nullable), `workspace_id` (UUID).

#### Scenario: Create deployment
- **WHEN** a ModelDeploymentModel is created with `model_id="gpt-4o"`, `channel="dev"`, `approval_status="pending"`
- **THEN** the record is persisted with the specified channel and pending approval

#### Scenario: Unique model per channel
- **WHEN** a deployment is created for a model_id + channel combination that already exists
- **THEN** the system SHALL reject the duplicate and return an error

### Requirement: Model promotion workflow
The system SHALL expose `/api/models/{model_id}/promote` endpoint that moves a model from one channel to the next (dev → staging → prod).

#### Scenario: Promote from dev to staging
- **WHEN** POST `/api/models/gpt-4o/promote` with `{"from": "dev", "to": "staging"}` is received
- **THEN** the system SHALL create a new ModelDeploymentModel with `channel="staging"`, `approval_status="pending"`

#### Scenario: Approve promotion
- **WHEN** POST `/api/models/gpt-4o/promote/{deployment_id}/approve` is received by a workspace admin
- **THEN** the system SHALL set `approval_status="approved"`, `approved_by=<user_id>`, `approved_at=now`

#### Scenario: Reject promotion
- **WHEN** POST `/api/models/gpt-4o/promote/{deployment_id}/reject` with reason is received
- **THEN** the system SHALL set `approval_status="rejected"` and keep the previous channel active

#### Scenario: Promotion without approval
- **WHEN** a pending deployment is used before approval
- **THEN** the system SHALL reject model invocations for that deployment with error "Deployment pending approval"

### Requirement: Model deprecation scheduling
The system SHALL expose `/api/models/{model_id}/deprecate` endpoint to schedule model deprecation with a sunset date.

#### Scenario: Schedule deprecation
- **WHEN** POST `/api/models/gpt-4o/deprecate` with `{"sunset_at": "2026-08-01T00:00:00Z"}` is received
- **THEN** the system SHALL set `deprecated_at=now` and `sunset_at` on the prod deployment

#### Scenario: Sunset notification at 30 days
- **WHEN** the sunset date is 30 days away
- **THEN** the system SHALL trigger an AlertService notification "Model gpt-4o sunsetting in 30 days"

#### Scenario: Automatic disable at sunset
- **WHEN** the current time exceeds `sunset_at`
- **THEN** the system SHALL set the deployment `is_enabled=False` and reject further invocations

#### Scenario: Cancel deprecation
- **WHEN** POST `/api/models/gpt-4o/deprecate/cancel` is received
- **THEN** the system SHALL clear `deprecated_at` and `sunset_at`, restoring normal operation

### Requirement: Deployment listing API
The system SHALL expose `/api/models/deployments` endpoint for listing all model deployments with their channel and approval status.

#### Scenario: List all deployments
- **WHEN** GET `/api/models/deployments` is received
- **THEN** the system SHALL return all deployments with model_id, channel, approval_status, version, deprecated_at, sunset_at

#### Scenario: Filter by channel
- **WHEN** GET `/api/models/deployments?channel=prod` is received
- **THEN** the system SHALL return only deployments in the prod channel

#### Scenario: Filter by approval status
- **WHEN** GET `/api/models/deployments?approval_status=pending` is received
- **THEN** the system SHALL return only pending deployments awaiting approval

### Requirement: Model rollback
The system SHALL expose `/api/models/{model_id}/rollback` endpoint to revert a model to a previous deployment version.

#### Scenario: Rollback to previous version
- **WHEN** POST `/api/models/gpt-4o/rollback` with `{"to_version": "v1.0"}` is received
- **THEN** the system SHALL create a new deployment pointing to the previous version and mark the current one as rolled_back

#### Scenario: Rollback creates audit trail
- **WHEN** a rollback is executed
- **THEN** the system SHALL record who initiated the rollback, when, and the reason in the deployment history
