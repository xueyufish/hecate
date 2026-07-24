## Why

Hecate's agent execution environment has critical security gaps compared to enterprise platforms (Bedrock AgentCore, Claude Code, Codex CLI, Dify, Google Vertex AI). Industry research across 15+ platforms (2025-2026) identified four P0 vulnerabilities: (1) DockerEnvironment has zero network egress control — agents can exfiltrate data to any external server; (2) `EXECUTE_SANDBOX` decisions from ToolAccessPolicy are not enforced — `sandbox_enabled` is just a flag; (3) security audit events are scattered in `logger.debug()` with no structured pipeline; (4) all tools can read all environment variables including API keys and database passwords. These gaps make Hecate unsuitable for production multi-tenant deployment without remediation.

## What Changes

### 9.12 Environment Network Egress Control
- Add `NetworkEgressPolicy` to DockerEnvironment with `allowedDomains` / `deniedDomains` configuration
- Implement egress traffic proxy with request logging for audit
- New config: `AGENT_ENV_NETWORK_POLICY=allow_all|deny_all` (default: `allow_all` for backward compatibility)
- Network namespace isolation per agent container
- When `deny_all`: only whitelisted domains reachable; all other egress blocked

### 9.13 Sandbox Enforcement Integration
- ToolWorker routes `EXECUTE_SANDBOX` decisions to DockerEnvironment `exec_shell()` instead of direct execution
- Applies to shell/exec tools (`bash`, `exec_shell`, `execute_code`) and `sandbox_enabled=True` MCP tools
- Container exit verification after tool execution (detect sandbox escape attempts)
- Security config version hash on agent config — warm pool containers invalidated when config changes
- New config: `AGENT_ENV_SANDBOX_ENFORCEMENT=false` (default: off for backward compatibility)

### 9.14 Structured Security Audit Pipeline
- New `SecurityAuditEvent` data model: tool_name, arguments, decision, reason, actor/agent_id, workspace_id, on_behalf_of_user, timestamp, policy_version, session_id
- New `SecurityAuditModel` ORM table with async batch write (in-memory buffer → flush every N events or T seconds)
- Every `ToolPolicyPipeline` + `ToolAccessPolicy` evaluation automatically emits audit events
- REST query API with filtering (by agent, workspace, decision, time range)
- Configurable retention (default 30 days, auto-cleanup)
- New config: `AGENT_ENV_AUDIT_ENABLED=true` (default: on — low risk, observation only)
- Applies to both LocalEnvironment and DockerEnvironment

### 9.15 Per-Execution Credential Scoping
- Strip secret environment variables from tool execution context before invocation
- Pattern-based detection: `*_KEY`, `*_SECRET`, `*_TOKEN`, `*_PASSWORD`, `*_API_KEY`, `*_PWD` + prefix `HECATE_SECRET_*`
- System variable whitelist always preserved: `PATH`, `HOME`, `LANG`, `LC_*`, `TMPDIR`, `USER`, `SHELL`
- Per-tool `CredentialScope` configuration mapping (which credentials each tool receives)
- Scoped credentials injected via secure context, not global env vars
- New config: `AGENT_ENV_CREDENTIAL_SCOPING=false` (default: off for backward compatibility)
- Applies to DockerEnvironment only (LocalEnvironment documented as development-only)

## Capabilities

### New Capabilities
- `environment-network-egress`: Per-environment application-level network egress control for DockerEnvironment — domain allowlist/blocklist, egress proxy, request logging
- `sandbox-enforcement`: Guarantees EXECUTE_SANDBOX decisions route through DockerEnvironment, with container exit verification and warm pool config invalidation
- `structured-security-audit`: Structured SecurityAuditEvent model, async batch storage, REST query API, automatic emission from policy evaluations, configurable retention
- `credential-scoping`: Runtime credential isolation — pattern-based secret env var stripping, per-tool credential injection, system variable whitelist

### Modified Capabilities
- `agent-environment`: DockerEnvironment gains NetworkEgressPolicy configuration and credential scoping integration; EnvironmentManager gains security config version tracking for warm pool invalidation
- `execution-security`: ToolAccessPolicy's `EXECUTE_SANDBOX` decision now has an enforcement mechanism in ToolWorker; AccessDecision evaluation emits SecurityAuditEvent
- `audit-logs`: Existing basic audit logging is extended by the structured SecurityAuditEvent pipeline; 8.7 SS5 SIEM Pipeline will consume 9.14 events as input

## Impact

### Code Changes
- `src/hecate/services/environment/docker_environment.py` — network egress policy, credential scoping
- `src/hecate/services/environment/manager.py` — security config version tracking, warm pool invalidation
- `src/hecate/services/environment/environment.py` — AgentEnvironment ABC gains optional security hooks
- `src/hecate/engine/tool_access.py` — emit SecurityAuditEvent on each evaluation
- `src/hecate/engine/policy_pipeline.py` — emit SecurityAuditEvent on visibility + execution evaluations
- `src/hecate/engine/workers/tool_worker.py` — EXECUTE_SANDBOX routing to DockerEnvironment
- `src/hecate/models/` — new SecurityAuditModel ORM table
- `src/hecate/core/config.py` — new AGENT_ENV_* security config settings
- `src/hecate/api/` — new REST endpoints for audit event query + network policy config

### Configuration
- `.env.example` — 5 new AGENT_ENV_* environment variables
- No breaking changes — all new features default to backward-compatible values

### Dependencies
- No new external packages required (uses existing asyncio, SQLAlchemy, FastAPI)
- Optional: iptables/ipset for advanced network isolation (Linux only, documented but not required)

### Testing
- Unit tests for NetworkEgressPolicy, CredentialScope, SecurityAuditEvent, SandboxEnforcementRouter
- Integration tests for DockerEnvironment with network egress + sandbox routing
- Unit tests for audit pipeline batch write + query API
- Engine-level tests for ToolPolicyPipeline/ToolAccessPolicy audit event emission
