## ADDED Requirements — 新增需求

### Requirement: Provider status change triggers agent warning — Provider 状态变更触发 agent 警告
When a provider's status changes to "error" or "inactive", the system SHALL identify agents using models from that provider and flag them for attention.

当 provider 的状态变为"error"或"inactive"时，系统应识别使用该 provider 模型的 agent 并标记它们以引起注意。

#### Scenario: Provider goes offline — Provider 离线
- **WHEN** a provider's status changes to "error"
- **THEN** agents using models from that provider show a warning indicator in the agent list
- **当**provider 的状态变为"error"
- **则**使用该 provider 模型的 agent 在 agent 列表中显示警告指示器

### Requirement: .env fallback for development — 开发环境的 .env 回退
The system SHALL continue to support API keys from environment variables as a fallback when no database providers are configured.

当没有配置数据库 provider 时，系统应继续支持从环境变量获取 API key 作为回退方案。

#### Scenario: No database providers configured — 未配置数据库 provider
- **WHEN** no providers exist in the database
- **THEN** /v1/models falls back to LiteLLM get_valid_models() using env var API keys
- **当**数据库中不存在任何 provider
- **则** /v1/models 回退到使用环境变量 API key 调用 LiteLLM get_valid_models()
