## ADDED Requirements

### Requirement: NetworkEgressPolicy configuration
The system SHALL provide a `NetworkEgressPolicy` configuration for DockerEnvironment that controls outbound network access from agent containers. The policy SHALL support `allow_all` mode (default, no restrictions) and `deny_all` mode (only whitelisted domains reachable). The global default SHALL be configurable via `AGENT_ENV_NETWORK_POLICY` setting.

#### Scenario: Default policy is allow_all
- **WHEN** `AGENT_ENV_NETWORK_POLICY` is not set
- **THEN** DockerEnvironment containers have unrestricted network access (backward compatible)

#### Scenario: Deny all policy blocks non-whitelisted domains
- **WHEN** `AGENT_ENV_NETWORK_POLICY=deny_all` and `allowedDomains=["pypi.org", "api.openai.com"]`
- **THEN** agent container can reach `pypi.org` and `api.openai.com`
- **AND** agent container cannot reach `evil.com` or any other non-whitelisted domain

#### Scenario: Denied domains override allowed domains
- **WHEN** `allowedDomains=["*.example.com"]` and `deniedDomains=["bad.example.com"]`
- **THEN** `api.example.com` is reachable
- **AND** `bad.example.com` is blocked even though it matches the allowed wildcard

#### Scenario: Per-agent policy overrides global default
- **WHEN** global `AGENT_ENV_NETWORK_POLICY=allow_all` but agent config has `network_policy: {mode: "deny_all", allowed_domains: ["api.github.com"]}`
- **THEN** that agent's container uses deny_all with only `api.github.com` reachable

### Requirement: Egress proxy for network isolation
The system SHALL route DockerEnvironment outbound traffic through an egress proxy container when `deny_all` policy is active. The proxy SHALL enforce domain-level access control and log all requests to the structured audit pipeline.

#### Scenario: Proxy container created lazily per workspace
- **WHEN** the first agent in workspace W has `deny_all` policy
- **THEN** an egress proxy container is created for workspace W
- **AND** subsequent agents in workspace W reuse the same proxy container

#### Scenario: Agent container attached to internal-only network
- **WHEN** an agent has `deny_all` policy
- **THEN** the agent's Docker container is attached to an internal Docker network with no internet gateway
- **AND** outbound traffic can only reach the egress proxy

#### Scenario: Proxy logs all requests
- **WHEN** agent container makes an HTTP request through the proxy
- **THEN** the proxy logs: timestamp, agent_id, workspace_id, destination domain, allowed/blocked, response status
- **AND** the log entry is emitted as a `SecurityAuditEvent`

### Requirement: LocalEnvironment network control warning
The system SHALL log a WARNING when network egress control is configured but `AGENT_ENV_BACKEND=local`.

#### Scenario: LocalEnvironment with deny_all policy
- **WHEN** `AGENT_ENV_BACKEND=local` and `AGENT_ENV_NETWORK_POLICY=deny_all`
- **THEN** the system logs a WARNING "Network egress control not available on LocalEnvironment"
- **AND** no network restrictions are applied
