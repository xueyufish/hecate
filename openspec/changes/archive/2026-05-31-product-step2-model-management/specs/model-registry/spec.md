## ADDED Requirements — 新增需求

### Requirement: Models are stored in database — 模型存储在数据库中
The system SHALL store discovered models in a model_registry table linked to their provider, with metadata including display name, type, capabilities, and context length.

系统应将发现的模型存储在 model_registry 表中，与其 provider 关联，元数据包括显示名称、类型、能力和上下文长度。

#### Scenario: Model registry populated on provider creation — 创建 provider 时填充模型注册表
- **WHEN** a provider is created and models are discovered
- **THEN** model_registry entries are created with provider_id, model_id, display_name, model_type="chat", capabilities, and is_enabled=true
- **当**创建 provider 并发现模型时
- **则**创建 model_registry 条目，包含 provider_id、model_id、display_name、model_type="chat"、capabilities 和 is_enabled=true

### Requirement: Admin can list all registered models — 管理员可以列出所有已注册模型
The system SHALL return all registered models grouped by provider with their metadata.

系统应返回按 provider 分组的所有已注册模型及其元数据。

#### Scenario: List models grouped by provider — 按 provider 分组列出模型
- **WHEN** admin requests GET /api/models
- **THEN** system returns models grouped by provider, each with id, model_id, display_name, model_type, capabilities, max_context, is_enabled
- **当**管理员请求 GET /api/models
- **则**系统返回按 provider 分组的模型，每个模型包含 id、model_id、display_name、model_type、capabilities、max_context、is_enabled

### Requirement: Admin can toggle model enabled status — 管理员可以切换模型启用状态
The system SHALL allow admins to enable or disable individual models.

系统应允许管理员启用或禁用单个模型。

#### Scenario: Disable a model — 禁用模型
- **WHEN** admin calls PUT /api/models/{id} with is_enabled=false
- **THEN** model is disabled and will not appear in user-facing /v1/models
- **当**管理员调用 PUT /api/models/{id}，设置 is_enabled=false
- **则**模型被禁用，不会出现在面向用户的 /v1/models 中

### Requirement: Admin can add custom models — 管理员可以添加自定义模型
The system SHALL allow admins to manually add models that are not auto-discovered.

系统应允许管理员手动添加未自动发现的模型。

#### Scenario: Add custom model — 添加自定义模型
- **WHEN** admin calls POST /api/models with model_id="custom-model", provider_id, display_name
- **THEN** model is created with is_custom=true and appears in the model list
- **当**管理员调用 POST /api/models，参数包含 model_id="custom-model"、provider_id、display_name
- **则**创建模型，is_custom=true，并出现在模型列表中
