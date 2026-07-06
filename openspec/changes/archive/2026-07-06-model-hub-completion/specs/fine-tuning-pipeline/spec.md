## ADDED Requirements

### Requirement: System manages fine-tuning datasets
The system SHALL provide `DatasetModel` with fields: name, description, format (jsonl/csv/json), version, row_count, schema_preview (JSON), file_storage_url (MinIO path), and workspace_id. Supports CRUD with versioning.

#### Scenario: Upload dataset
- **WHEN** a user uploads a JSONL file for fine-tuning
- **THEN** the system stores the file in MinIO, creates a DatasetModel record with row_count, format, and schema preview (first 5 rows)

#### Scenario: Dataset versioning
- **WHEN** a user uploads a new version of an existing dataset
- **THEN** the system creates a new DatasetModel with incremented version, preserving the previous version

#### Scenario: Dataset preview
- **WHEN** a user requests a dataset preview
- **THEN** the system returns the first 10 rows in a structured format suitable for frontend rendering

### Requirement: FineTuningBackendABC defines provider-agnostic interface
The system SHALL define `FineTuningBackendABC` with abstract methods: `submit_job(dataset, base_model, config)`, `poll_status(job_id)`, `cancel_job(job_id)`, `get_result(job_id)`.

#### Scenario: Submit fine-tuning job
- **WHEN** `submit_job(dataset_id, base_model="gpt-4o", config={epochs: 3, batch_size: 32})` is called
- **THEN** the backend SHALL return a `FineTuningJobModel` with `status: "queued"` and provider-specific job ID

#### Scenario: Poll job status
- **WHEN** `poll_status(job_id)` is called for a running job
- **THEN** the backend SHALL return current status (`queued`/`running`/`succeeded`/`failed`), progress percentage, and any metrics available

#### Scenario: Cancel job
- **WHEN** `cancel_job(job_id)` is called
- **THEN** the backend SHALL cancel the provider job and update `FineTuningJobModel.status` to `cancelled`

### Requirement: OpenAI fine-tuning adapter implements FineTuningBackendABC
The system SHALL provide `OpenAIFineTuningBackend` that implements `FineTuningBackendABC` using the OpenAI fine-tuning API (`/v1/files` for upload, `/v1/fine_tuning/jobs` for job management).

#### Scenario: Upload dataset to OpenAI
- **WHEN** a fine-tuning job is submitted with a local dataset
- **THEN** the adapter SHALL upload the dataset file to OpenAI's Files endpoint with `purpose: "fine-tune"` and receive a `file_id`

#### Scenario: Create OpenAI fine-tuning job
- **WHEN** the adapter calls `submit_job` with file_id, base_model, and hyperparameters
- **THEN** the adapter SHALL call `POST /v1/fine_tuning/jobs` and store the returned job ID in `FineTuningJobModel`

#### Scenario: Job completion registers fine-tuned model
- **WHEN** a fine-tuning job reaches `status: "succeeded"`
- **THEN** the system SHALL register the `fine_tuned_model` ID in `ModelRegistryModel` with metadata indicating it is a fine-tuned variant of the base model

### Requirement: System tracks fine-tuning job lifecycle
The system SHALL persist `FineTuningJobModel` with fields: dataset_id, base_model, provider, provider_job_id, status, config (hyperparameters), result_model_id, metrics (training loss, validation loss), error_message, workspace_id, created_at, updated_at.

#### Scenario: Job state transitions
- **WHEN** a job transitions from `running` to `succeeded`
- **THEN** the system SHALL update status, store result metrics from the provider, and populate `result_model_id`

#### Scenario: Job failure
- **WHEN** a job fails
- **THEN** the system SHALL update status to `failed` and store the provider error message in `error_message`

### Requirement: System supports one-click deployment of fine-tuned models
The system SHALL enable deploying a fine-tuned model to an inference endpoint (6.5) or making it available via the existing LLM Service with a single action.

#### Scenario: Deploy fine-tuned model
- **WHEN** a user clicks "Deploy" on a completed fine-tuning job
- **THEN** the system registers the fine-tuned model in the catalog and makes it available for agent configuration
