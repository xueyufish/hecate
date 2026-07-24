## ADDED Requirements

### Requirement: DockerEnvironment network egress policy support
DockerEnvironment SHALL accept an optional `NetworkEgressPolicy` configuration that controls outbound network access from the container. When the policy mode is `deny_all`, the container SHALL be attached to an internal-only Docker network with traffic routed through an egress proxy.

#### Scenario: DockerEnvironment created with deny_all policy
- **WHEN** DockerEnvironment is created with `network_policy={mode: "deny_all", allowed_domains: ["pypi.org"]}`
- **THEN** the container is attached to an internal Docker network with no internet gateway
- **AND** outbound traffic is routed through the workspace egress proxy

#### Scenario: DockerEnvironment created with allow_all policy
- **WHEN** DockerEnvironment is created with `network_policy={mode: "allow_all"}` or no network policy
- **THEN** the container uses the default Docker bridge network with unrestricted internet access

### Requirement: EnvironmentManager security config hash tracking
EnvironmentManager SHALL compute and store a `security_config_hash` for each agent based on its effective security configuration (network policy, credential scope, sandbox enforcement settings). When the hash changes, warm pool containers for that agent SHALL be invalidated.

#### Scenario: Security config hash computed on agent creation
- **WHEN** an agent is created or updated with security config
- **THEN** EnvironmentManager computes `security_config_hash` from the config
- **AND** stores the hash alongside the agent's environment metadata

#### Scenario: Hash change invalidates warm pool container
- **WHEN** agent security config is updated and the hash changes
- **THEN** any warm pool containers for that agent are marked for destruction
- **AND** the next `get_or_create()` call creates a fresh container with updated config

### Requirement: DockerEnvironment credential scope support
DockerEnvironment SHALL accept an optional `CredentialScope` configuration that determines which environment variables are passed to tool execution. When credential scoping is enabled, secret-pattern environment variables SHALL be stripped and only scoped credentials injected.

#### Scenario: Tool execution with credential scope
- **WHEN** DockerEnvironment has `credential_scoping=true` and a tool executes with scope `["API_TOKEN"]`
- **THEN** the tool subprocess environment contains only whitelisted system vars + `API_TOKEN`
