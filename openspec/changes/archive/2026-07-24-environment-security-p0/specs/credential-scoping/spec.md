## ADDED Requirements — 新增需求

### Requirement: CredentialScope configuration — 需求：CredentialScope 配置
The system SHALL provide a `CredentialScope` configuration that maps tools to the credentials they are allowed to receive at execution time. Tools without a configured scope SHALL receive a sanitized environment with secret variables stripped.

系统应提供一个 `CredentialScope` 配置，将工具映射到它们在执行时允许接收的凭据。没有配置范围的工具应收到一个经过清理的环境，剥离秘密变量。

#### Scenario: Tool with configured credential scope — 场景：配置了凭据范围的工具
- **WHEN** tool `salesforce_connector` has `credential_scope: ["SALESFORCE_TOKEN", "SALESFORCE_INSTANCE_URL"]`
- **THEN** the tool's execution environment contains only `SALESFORCE_TOKEN` and `SALESFORCE_INSTANCE_URL` from the secret store
- **AND** no other secret variables are present

- **当**工具 `salesforce_connector` 具有 `credential_scope: ["SALESFORCE_TOKEN", "SALESFORCE_INSTANCE_URL"]`
- **则**工具的执行环境仅包含来自秘密存储的 `SALESFORCE_TOKEN` 和 `SALESFORCE_INSTANCE_URL`
- **且**不存在其他秘密变量

#### Scenario: Tool without configured scope gets sanitized env — 场景：未配置范围的工具获取清理环境
- **WHEN** tool `web_search` has no `credential_scope` configured
- **THEN** the tool's execution environment contains only system whitelist variables (PATH, HOME, etc.)
- **AND** no secret variables are present

- **当**工具 `web_search` 未配置 `credential_scope`
- **则**工具的执行环境仅包含系统白名单变量（PATH、HOME 等）
- **且**不存在秘密变量

### Requirement: Pattern-based secret stripping — 需求：基于模式的秘密剥离
The system SHALL strip environment variables matching secret patterns before tool execution in DockerEnvironment. Patterns SHALL include: `*_KEY`, `*_SECRET`, `*_TOKEN`, `*_PASSWORD`, `*_API_KEY`, `*_PWD`, and prefix `HECATE_SECRET_*`.

系统应在 DockerEnvironment 中执行工具前剥离匹配秘密模式的环境变量。模式应包括：`*_KEY`、`*_SECRET`、`*_TOKEN`、`*_PASSWORD`、`*_API_KEY`、`*_PWD` 和前缀 `HECATE_SECRET_*`。

#### Scenario: API key stripped from tool environment — 场景：从工具环境中剥离 API 密钥
- **WHEN** the process environment contains `OPENAI_API_KEY=sk-xxx` and a tool executes in DockerEnvironment
- **THEN** the tool's execution environment does NOT contain `OPENAI_API_KEY`

- **当**进程环境包含 `OPENAI_API_KEY=sk-xxx` 且工具在 DockerEnvironment 中执行
- **则**工具的执行环境不包含 `OPENAI_API_KEY`

#### Scenario: HECATE_SECRET prefix stripped — 场景：剥离 HECATE_SECRET 前缀
- **WHEN** the process environment contains `HECATE_SECRET_DB_PASSWORD=pass123`
- **THEN** the tool's execution environment does NOT contain `HECATE_SECRET_DB_PASSWORD`

- **当**进程环境包含 `HECATE_SECRET_DB_PASSWORD=pass123`
- **则**工具的执行环境不包含 `HECATE_SECRET_DB_PASSWORD`

#### Scenario: Custom pattern stripping — 场景：自定义模式剥离
- **WHEN** workspace config specifies custom strip pattern `*_CONNECTION_STRING`
- **AND** the environment contains `REDIS_CONNECTION_STRING=redis://...`
- **THEN** the tool's execution environment does NOT contain `REDIS_CONNECTION_STRING`

- **当**工作空间配置指定自定义剥离模式 `*_CONNECTION_STRING`
- **且**环境包含 `REDIS_CONNECTION_STRING=redis://...`
- **则**工具的执行环境不包含 `REDIS_CONNECTION_STRING`

### Requirement: System variable whitelist preservation — 需求：系统变量白名单保留
The system SHALL always preserve essential system environment variables regardless of stripping patterns.

无论剥离模式如何，系统应始终保留基本的系统环境变量。

#### Scenario: PATH and HOME preserved — 场景：保留 PATH 和 HOME
- **WHEN** credential scoping strips secret patterns
- **THEN** `PATH`, `HOME`, `LANG`, `LC_*`, `TMPDIR`, `USER`, `SHELL`, `HOSTNAME`, `TERM`, `PWD` are preserved in the tool's execution environment

- **当**凭据范围剥离秘密模式
- **则** `PATH`、`HOME`、`LANG`、`LC_*`、`TMPDIR`、`USER`、`SHELL`、`HOSTNAME`、`TERM`、`PWD` 在工具的执行环境中保留

### Requirement: Credential scoping applies to DockerEnvironment only — 需求：凭据范围仅适用于 DockerEnvironment
The system SHALL apply credential scoping only when `AGENT_ENV_BACKEND=docker`.

系统仅应在 `AGENT_ENV_BACKEND=docker` 时应用凭据范围。

#### Scenario: DockerEnvironment with credential scoping — 场景：带凭据范围的 DockerEnvironment
- **WHEN** `AGENT_ENV_BACKEND=docker` and `AGENT_ENV_CREDENTIAL_SCOPING=true`
- **THEN** tools executing in DockerEnvironment receive sanitized + scoped credentials

- **当** `AGENT_ENV_BACKEND=docker` 且 `AGENT_ENV_CREDENTIAL_SCOPING=true`
- **则**在 DockerEnvironment 中执行的工具接收清理 + 范围限定的凭据

#### Scenario: LocalEnvironment warns on credential scoping config — 场景：LocalEnvironment 对凭据范围配置发出警告
- **WHEN** `AGENT_ENV_BACKEND=local` and `AGENT_ENV_CREDENTIAL_SCOPING=true`
- **THEN** the system logs WARNING "Credential scoping not available on LocalEnvironment"
- **AND** no credential stripping occurs

- **当** `AGENT_ENV_BACKEND=local` 且 `AGENT_ENV_CREDENTIAL_SCOPING=true`
- **则**系统记录警告"凭据范围在 LocalEnvironment 上不可用"
- **且**不进行凭据剥离

#### Scenario: Credential scoping disabled by default — 场景：凭据范围默认禁用
- **WHEN** `AGENT_ENV_CREDENTIAL_SCOPING` is not set
- **THEN** all environment variables are passed to tool execution (backward compatible)

- **当**未设置 `AGENT_ENV_CREDENTIAL_SCOPING`
- **则**所有环境变量传递给工具执行（向后兼容）
