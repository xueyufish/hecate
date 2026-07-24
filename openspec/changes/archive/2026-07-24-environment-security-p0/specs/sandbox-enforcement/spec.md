## ADDED Requirements

### Requirement: EXECUTE_SANDBOX routing to DockerEnvironment
The system SHALL route tool execution to DockerEnvironment when `ToolAccessPolicy.evaluate()` returns `EXECUTE_SANDBOX` and sandbox enforcement is enabled. The routing SHALL apply to shell/exec tools and `sandbox_enabled` MCP tools.

#### Scenario: Shell tool with EXECUTE_SANDBOX routes to container
- **WHEN** `AGENT_ENV_SANDBOX_ENFORCEMENT=true` and `ToolAccessPolicy.evaluate()` returns `EXECUTE_SANDBOX` for tool `bash`
- **THEN** the tool executes inside the agent's DockerEnvironment container via `exec_shell()`
- **AND** the tool does NOT execute directly on the host

#### Scenario: Sandbox enforcement disabled by default
- **WHEN** `AGENT_ENV_SANDBOX_ENFORCEMENT` is not set (default `false`)
- **THEN** `EXECUTE_SANDBOX` decision is treated as `EXECUTE` (current behavior, backward compatible)

#### Scenario: Python built-in tools not routed to container
- **WHEN** sandbox enforcement is enabled and tool `read_file` gets `EXECUTE_SANDBOX`
- **THEN** the tool executes directly (governed by WorkspaceBoundaryPolicy)
- **AND** no container routing occurs for Python function tools

#### Scenario: MCP tool with sandbox_enabled routes to container
- **WHEN** sandbox enforcement is enabled and an MCP tool with `sandbox_enabled=True` gets `EXECUTE_SANDBOX`
- **THEN** the MCP tool call is executed inside the agent's DockerEnvironment container

### Requirement: Container exit verification
The system SHALL verify container health after sandboxed tool execution. If the container process exited abnormally, the system SHALL emit a security audit event.

#### Scenario: Normal container exit
- **WHEN** a sandboxed tool completes and the container is still running with normal return code
- **THEN** no anomaly event is emitted

#### Scenario: Abnormal container exit detected
- **WHEN** a sandboxed tool completes but `proc.returncode` indicates the container process was killed (OOM, segfault)
- **THEN** a `SecurityAuditEvent` with `decision="sandbox_anomaly"` is emitted
- **AND** a WARNING is logged with container ID and exit code

### Requirement: Warm pool config invalidation on security config change
The system SHALL invalidate warm pool containers when an agent's security configuration changes.

#### Scenario: Security config hash changes on agent update
- **WHEN** an agent's security config (network policy, credential scope, sandbox enforcement) is updated
- **THEN** the agent's `security_config_hash` changes
- **AND** any warm pool containers for that agent are destroyed (not reused)
- **AND** the next `get_or_create()` creates a fresh container with updated config

#### Scenario: Unrelated config change does not invalidate
- **WHEN** an agent's non-security config (e.g., display name) is updated
- **THEN** the `security_config_hash` does not change
- **AND** warm pool containers remain valid for reuse
