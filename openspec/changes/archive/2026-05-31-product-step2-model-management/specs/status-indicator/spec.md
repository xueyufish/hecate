## ADDED Requirements

### Requirement: Provider status is tracked
Each provider SHALL have a status field indicating "active", "inactive", or "error".

#### Scenario: Provider status on creation
- **WHEN** a provider is created with a valid API key
- **THEN** provider status is set to "active"

#### Scenario: Provider status on connectivity test failure
- **WHEN** a provider connectivity test fails
- **THEN** provider status is updated to "error"

### Requirement: Provider status is displayed in admin UI
The provider list page SHALL visually indicate each provider's status with color-coded badges.

#### Scenario: Display provider status
- **WHEN** admin views the provider list
- **THEN** each provider shows a status badge: green for "active", gray for "inactive", red for "error"
