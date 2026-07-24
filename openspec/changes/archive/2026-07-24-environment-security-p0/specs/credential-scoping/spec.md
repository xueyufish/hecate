## ADDED Requirements

### Requirement: CredentialScope configuration
The system SHALL provide a `CredentialScope` configuration that maps tools to the credentials they are allowed to receive at execution time. Tools without a configured scope SHALL receive a sanitized environment with secret variables stripped.

#### Scenario: Tool with configured credential scope
- **WHEN** tool `salesforce_connector` has `credential_scope: ["SALESFORCE_TOKEN", "SALESFORCE_INSTANCE_URL"]`
- **THEN** the tool's execution environment contains only `SALESFORCE_TOKEN` and `SALESFORCE_INSTANCE_URL` from the secret store
- **AND** no other secret variables are present

#### Scenario: Tool without configured scope gets sanitized env
- **WHEN** tool `web_search` has no `credential_scope` configured
- **THEN** the tool's execution environment contains only system whitelist variables (PATH, HOME, etc.)
- **AND** no secret variables are present

### Requirement: Pattern-based secret stripping
The system SHALL strip environment variables matching secret patterns before tool execution in DockerEnvironment. Patterns SHALL include: `*_KEY`, `*_SECRET`, `*_TOKEN`, `*_PASSWORD`, `*_API_KEY`, `*_PWD`, and prefix `HECATE_SECRET_*`.

#### Scenario: API key stripped from tool environment
- **WHEN** the process environment contains `OPENAI_API_KEY=sk-xxx` and a tool executes in DockerEnvironment
- **THEN** the tool's execution environment does NOT contain `OPENAI_API_KEY`

#### Scenario: HECATE_SECRET prefix stripped
- **WHEN** the process environment contains `HECATE_SECRET_DB_PASSWORD=pass123`
- **THEN** the tool's execution environment does NOT contain `HECATE_SECRET_DB_PASSWORD`

#### Scenario: Custom pattern stripping
- **WHEN** workspace config specifies custom strip pattern `*_CONNECTION_STRING`
- **AND** the environment contains `REDIS_CONNECTION_STRING=redis://...`
- **THEN** the tool's execution environment does NOT contain `REDIS_CONNECTION_STRING`

### Requirement: System variable whitelist preservation
The system SHALL always preserve essential system environment variables regardless of stripping patterns.

#### Scenario: PATH and HOME preserved
- **WHEN** credential scoping strips secret patterns
- **THEN** `PATH`, `HOME`, `LANG`, `LC_*`, `TMPDIR`, `USER`, `SHELL`, `HOSTNAME`, `TERM`, `PWD` are preserved in the tool's execution environment

### Requirement: Credential scoping applies to DockerEnvironment only
The system SHALL apply credential scoping only when `AGENT_ENV_BACKEND=docker`.

#### Scenario: DockerEnvironment with credential scoping
- **WHEN** `AGENT_ENV_BACKEND=docker` and `AGENT_ENV_CREDENTIAL_SCOPING=true`
- **THEN** tools executing in DockerEnvironment receive sanitized + scoped credentials

#### Scenario: LocalEnvironment warns on credential scoping config
- **WHEN** `AGENT_ENV_BACKEND=local` and `AGENT_ENV_CREDENTIAL_SCOPING=true`
- **THEN** the system logs WARNING "Credential scoping not available on LocalEnvironment"
- **AND** no credential stripping occurs

#### Scenario: Credential scoping disabled by default
- **WHEN** `AGENT_ENV_CREDENTIAL_SCOPING` is not set
- **THEN** all environment variables are passed to tool execution (backward compatible)
