## ADDED Requirements — 新增需求

### Requirement: Provider status is tracked — Provider 状态被跟踪
Each provider SHALL have a status field indicating "active", "inactive", or "error".

每个 provider 应有一个状态字段，指示"active"（活跃）、"inactive"（非活跃）或"error"（错误）。

#### Scenario: Provider status on creation — 创建时的 provider 状态
- **WHEN** a provider is created with a valid API key
- **THEN** provider status is set to "active"
- **当**使用有效的 API key 创建 provider
- **则**provider 状态设置为"active"

#### Scenario: Provider status on connectivity test failure — 连接测试失败时的 provider 状态
- **WHEN** a provider connectivity test fails
- **THEN** provider status is updated to "error"
- **当**provider 连接测试失败
- **则**provider 状态更新为"error"

### Requirement: Provider status is displayed in admin UI — Provider 状态在管理 UI 中显示
The provider list page SHALL visually indicate each provider's status with color-coded badges.

provider 列表页面应使用颜色编码的徽章直观地指示每个 provider 的状态。

#### Scenario: Display provider status — 显示 provider 状态
- **WHEN** admin views the provider list
- **THEN** each provider shows a status badge: green for "active", gray for "inactive", red for "error"
- **当**管理员查看 provider 列表
- **则**每个 provider 显示状态徽章：绿色表示"active"，灰色表示"inactive"，红色表示"error"
