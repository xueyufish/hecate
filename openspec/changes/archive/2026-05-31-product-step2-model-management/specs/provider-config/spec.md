## ADDED Requirements — 新增需求

### Requirement: Provider has configurable timeout, retry, and rate limit — Provider 具有可配置的超时、重试和速率限制
Each provider SHALL store optional configuration for timeout (seconds), max_retries, and rate_limit_rpm in a JSON config field.

每个 provider 应在 JSON 配置字段中存储可选的 timeout（秒）、max_retries 和 rate_limit_rpm 配置。

#### Scenario: Create provider with custom config — 使用自定义配置创建 provider
- **WHEN** admin creates a provider with config={"timeout": 60, "max_retries": 5}
- **THEN** config is stored and used when calling models through this provider
- **当**管理员创建 provider，config={"timeout": 60, "max_retries": 5}
- **则**配置被存储并在通过此 provider 调用模型时使用

#### Scenario: Default config values — 默认配置值
- **WHEN** admin creates a provider without specifying config
- **THEN** system applies defaults: timeout=30, max_retries=3, rate_limit_rpm=60
- **当**管理员创建 provider 时未指定 config
- **则**系统应用默认值：timeout=30、max_retries=3、rate_limit_rpm=60
