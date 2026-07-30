## ADDED Requirements — 新增需求

### Requirement: 系统管理微调数据集 — System manages fine-tuning datasets
系统应提供 `DatasetModel`，字段包括：name、description、format（jsonl/csv/json）、version、row_count、schema_preview（JSON）、file_storage_url（MinIO 路径）和 workspace_id。支持带版本控制的 CRUD。

#### Scenario: 上传数据集 — Upload dataset
- **WHEN** 用户上传用于微调的 JSONL 文件
- **THEN** 系统将文件存储在 MinIO 中，创建 DatasetModel 记录，包含 row_count、format 和 schema_preview（前 5 行）

#### Scenario: 数据集版本控制 — Dataset versioning
- **WHEN** 用户上传现有数据集的新版本
- **THEN** 系统创建新的 DatasetModel，版本号递增，保留前一个版本

#### Scenario: 数据集预览 — Dataset preview
- **WHEN** 用户请求数据集预览
- **THEN** 系统以适合前端渲染的格式返回前 10 行

### Requirement: FineTuningBackendABC 定义提供商无关的接口 — FineTuningBackendABC defines provider-agnostic interface
系统应定义 `FineTuningBackendABC`，包含抽象方法：`submit_job(dataset, base_model, config)`、`poll_status(job_id)`、`cancel_job(job_id)`、`get_result(job_id)`。

#### Scenario: 提交微调任务 — Submit fine-tuning job
- **WHEN** 调用 `submit_job(dataset_id, base_model="gpt-4o", config={epochs: 3, batch_size: 32})`
- **THEN** 后端应返回 `FineTuningJobModel`，状态为 `"queued"`，并包含提供商特定的任务 ID

#### Scenario: 轮询任务状态 — Poll job status
- **WHEN** 对运行中的任务调用 `poll_status(job_id)`
- **THEN** 后端应返回当前状态（`queued`/`running`/`succeeded`/`failed`）、进度百分比和任何可用的指标

#### Scenario: 取消任务 — Cancel job
- **WHEN** 调用 `cancel_job(job_id)`
- **THEN** 后端应取消提供商任务，并将 `FineTuningJobModel.status` 更新为 `cancelled`

### Requirement: OpenAI 微调适配器实现 FineTuningBackendABC — OpenAI fine-tuning adapter implements FineTuningBackendABC
系统应提供 `OpenAIFineTuningBackend`，使用 OpenAI 微调 API（用于上传的 `/v1/files`，用于任务管理的 `/v1/fine_tuning/jobs`）实现 `FineTuningBackendABC`。

#### Scenario: 向 OpenAI 上传数据集 — Upload dataset to OpenAI
- **WHEN** 使用本地数据集提交微调任务
- **THEN** 适配器应将数据集文件上传到 OpenAI 的 Files 端点，设置 `purpose: "fine-tune"`，并接收一个 `file_id`

#### Scenario: 创建 OpenAI 微调任务 — Create OpenAI fine-tuning job
- **WHEN** 适配器使用 file_id、base_model 和超参数调用 `submit_job`
- **THEN** 适配器应调用 `POST /v1/fine_tuning/jobs` 并将返回的作业 ID 存储在 `FineTuningJobModel` 中

#### Scenario: 任务完成注册微调模型 — Job completion registers fine-tuned model
- **WHEN** 微调任务达到 `status: "succeeded"`
- **THEN** 系统应将 `fine_tuned_model` ID 注册到 `ModelRegistryModel` 中，元数据指示它是对应基础模型的微调变体

### Requirement: 系统跟踪微调任务生命周期 — System tracks fine-tuning job lifecycle
系统应持久化 `FineTuningJobModel`，字段包括：dataset_id、base_model、provider、provider_job_id、status、config（超参数）、result_model_id、metrics（训练损失、验证损失）、error_message、workspace_id、created_at、updated_at。

#### Scenario: 任务状态转换 — Job state transitions
- **WHEN** 任务从 `running` 转换为 `succeeded`
- **THEN** 系统应更新状态，存储来自提供商的结果指标，并填充 `result_model_id`

#### Scenario: 任务失败 — Job failure
- **WHEN** 任务失败
- **THEN** 系统应将状态更新为 `failed`，并将提供商的错误消息存储在 `error_message` 中

### Requirement: 系统支持一键部署微调模型 — System supports one-click deployment of fine-tuned models
系统应支持将微调模型部署到推理端点（6.5）或通过现有 LLM Service 提供，只需一次操作。

#### Scenario: 部署微调模型 — Deploy fine-tuned model
- **WHEN** 用户在完成的微调任务上点击"部署"
- **THEN** 系统将微调模型注册到目录中，并使其可用于 Agent 配置
