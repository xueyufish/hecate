## 1. Configuration & Data Models (Foundation)

- [x] 1.1 Add security config settings to `core/config.py`: `AGENT_ENV_NETWORK_POLICY` (default: `allow_all`), `AGENT_ENV_AUDIT_ENABLED` (default: `true`), `AGENT_ENV_AUDIT_RETENTION_DAYS` (default: `30`), `AGENT_ENV_CREDENTIAL_SCOPING` (default: `false`), `AGENT_ENV_SANDBOX_ENFORCEMENT` (default: `false`)
- [x] 1.2 Update `.env.example` with 5 new `AGENT_ENV_*` variables and documentation comments
- [x] 1.3 Create `SecurityAuditModel` ORM table in `models/security_audit.py` — fields: id (UUID PK), agent_id (indexed), workspace_id (indexed), session_id (nullable), tool_name, arguments_hash (SHA-256), decision, reason, policy_version, on_behalf_of_user (nullable), timestamp (indexed), layer_results (JSON)
- [x] 1.4 Create `SecurityAuditCreateSchema` / `SecurityAuditReadSchema` Pydantic schemas in `models/security_audit.py`
- [x] 1.5 Add Alembic migration for `security_audit_events` table with indexes on (agent_id, timestamp) and (workspace_id, timestamp)
- [x] 1.6 Create `NetworkEgressPolicy` dataclass in `services/environment/network_policy.py` — fields: mode (`allow_all`/`deny_all`), allowed_domains (list[str]), denied_domains (list[str])
- [x] 1.7 Create `CredentialScope` dataclass in `services/environment/credential_scope.py` — fields: enabled (bool), strip_patterns (list[str]), whitelist (set[str]), custom_patterns (list[str]), tool_credentials (dict[str, list[str]])

## 2. Structured Security Audit Pipeline (9.14)

- [x] 2.1 Create `AuditSink` ABC in `engine/audit_sink.py` — abstract method `emit(event: dict) -> None` (maintains engine layer zero-dependency: engine defines interface, services provide implementation)
- [x] 2.2 Create `SecurityAuditEmitter` in `engine/audit_sink.py` — collects events into async buffer, flushes every 50 events or 5 seconds via `AuditSink`
- [x] 2.3 Add audit emission to `ToolPolicyPipeline.evaluate_visibility()` — emit event per tool when HIDE/DENY decision reached
- [x] 2.4 Add audit emission to `ToolPolicyPipeline.evaluate_execution()` — emit event with final decision + per-layer LayerResult breakdown
- [x] 2.5 Add audit emission to `ToolAccessPolicy.evaluate()` — emit event with AccessDecision, matched rules, risk level, policy_version
- [x] 2.6 Implement `SecurityAuditService` in `services/security/audit_service.py` — implements `AuditSink`, writes to `SecurityAuditModel` via async batch writer
- [x] 2.7 Implement async batch writer in `SecurityAuditService` — in-memory deque buffer, background task flushes every 50 events or 5 seconds, flush on shutdown
- [x] 2.8 Implement audit retention cleanup task — daily background task deletes rows older than `AGENT_ENV_AUDIT_RETENTION_DAYS`
- [x] 2.9 Create REST API endpoints in `api/security_audit.py`: `GET /api/security/audit` with query params (agent_id, workspace_id, decision, start, end, limit, offset) + pagination response
- [x] 2.10 Wire `SecurityAuditService` into DI container as singleton; inject into engine via `EnginePort` or execution context
- [x] 2.11 Write unit tests for `SecurityAuditEmitter` buffer + flush behavior
- [x] 2.12 Write unit tests for `SecurityAuditService` batch write + retention cleanup
- [x] 2.13 Write unit tests for REST API query filtering and pagination
- [x] 2.14 Write unit tests verifying audit events emitted from ToolPolicyPipeline (visibility + execution) and ToolAccessPolicy

## 3. Sandbox Enforcement Integration (9.13)

- [x] 3.1 Create `SandboxEnforcementRouter` in `engine/workers/sandbox_router.py` — inspects `AccessDecision`, routes `EXECUTE_SANDBOX` to DockerEnvironment for shell/exec tools when enforcement enabled
- [x] 3.2 Implement tool category classification in `SandboxEnforcementRouter` — determine if tool is shell/exec (route to container), MCP sandboxed (route to container), or Python built-in (execute directly)
- [x] 3.3 Integrate `SandboxEnforcementRouter` into `ToolWorker` — between policy evaluation and tool execution, check `AGENT_ENV_SANDBOX_ENFORCEMENT` flag
- [x] 3.4 Implement container exit verification — after `exec_shell()`, check return code; emit `SecurityAuditEvent` with `decision="sandbox_anomaly"` on abnormal exit
- [x] 3.5 Implement `security_config_hash` computation in `EnvironmentManager` — hash of network policy + credential scope + sandbox enforcement config per agent
- [x] 3.6 Implement warm pool invalidation on `security_config_hash` change — destroy old containers, force fresh creation on next `get_or_create()`
- [x] 3.7 Write unit tests for `SandboxEnforcementRouter` routing decisions (shell tool → container, Python tool → direct, MCP tool → container)
- [x] 3.8 Write unit tests for container exit verification + anomaly event emission
- [x] 3.9 Write unit tests for warm pool config hash invalidation
- [x] 3.10 Write integration test: ToolWorker with enforcement enabled routes bash tool to DockerEnvironment

## 4. Network Egress Control (9.12)

- [x] 4.1 Implement egress proxy lifecycle management in `services/environment/egress_proxy.py` — lazy-create per workspace, warm pool, Squid or lightweight HTTP CONNECT proxy
- [x] 4.2 Implement Docker network creation for `deny_all` mode — internal-only network per workspace, no internet gateway
- [x] 4.3 Update `DockerEnvironment.__init__` to accept optional `NetworkEgressPolicy` — when `deny_all`, attach container to internal network + configure proxy
- [x] 4.4 Implement domain allowlist/blocklist enforcement in egress proxy — derive config from `NetworkEgressPolicy.allowed_domains` / `denied_domains`
- [x] 4.5 Implement proxy request logging — each request emits `SecurityAuditEvent` with destination domain, allowed/blocked, response status
- [x] 4.6 Update `EnvironmentManager` to pass `NetworkEgressPolicy` to DockerEnvironment based on global config + per-agent override
- [x] 4.7 Implement LocalEnvironment warning — log WARNING when `AGENT_ENV_NETWORK_POLICY=deny_all` and backend is local
- [x] 4.8 Write unit tests for `NetworkEgressPolicy` configuration parsing and validation
- [x] 4.9 Write unit tests for egress proxy allowlist/blocklist logic
- [x] 4.10 Write integration test: DockerEnvironment with deny_all policy blocks non-whitelained domain access (mocked proxy)

## 5. Per-Execution Credential Scoping (9.15)

- [x] 5.1 Implement credential pattern detection in `services/environment/credential_scope.py` — match `*_KEY`, `*_SECRET`, `*_TOKEN`, `*_PASSWORD`, `*_API_KEY`, `*_PWD`, `HECATE_SECRET_*` prefix, custom patterns
- [x] 5.2 Implement system variable whitelist — `PATH`, `HOME`, `LANG`, `LC_*`, `TMPDIR`, `USER`, `SHELL`, `HOSTNAME`, `TERM`, `PWD` always preserved
- [x] 5.3 Implement environment sanitization function — given full env dict + CredentialScope, return sanitized dict with secrets stripped and scoped credentials injected
- [x] 5.4 Update `DockerEnvironment.exec_shell()` to apply credential scoping before command execution when `AGENT_ENV_CREDENTIAL_SCOPING=true`
- [x] 5.5 Implement LocalEnvironment warning — log WARNING when credential scoping enabled and backend is local
- [x] 5.6 Write unit tests for credential pattern detection (all patterns + custom + prefix)
- [x] 5.7 Write unit tests for whitelist preservation (system vars never stripped)
- [x] 5.8 Write unit tests for environment sanitization with CredentialScope configuration
- [x] 5.9 Write integration test: DockerEnvironment with credential scoping strips OPENAI_API_KEY from tool subprocess env

## 6. Integration & Cross-Feature Tests

- [x] 6.1 Write end-to-end test: agent attempts `curl` to non-whitelisted domain → blocked by network policy → audit event recorded → credential not leaked
- [x] 6.2 Write end-to-end test: EXECUTE_SANDBOX bash tool → executes in container → audit event with per-layer breakdown → container health verified
- [x] 6.3 Write test: all features disabled (defaults) → zero behavioral change from existing agents
- [x] 6.4 Write test: audit pipeline works on LocalEnvironment (emits events, queryable via API)
- [x] 6.5 Write test: warm pool reuse with unchanged security config (container reused)
- [x] 6.6 Write test: warm pool invalidation on security config change (container destroyed + recreated)

## 7. Documentation & Cleanup

- [x] 7.1 Update `docs/design/security-architecture.md` with Environment Security P0 section
- [x] 7.2 Document LocalEnvironment limitations in `docs/design/` — "development only, not for production"
- [x] 7.3 Run `ruff check src/hecate/ tests/ && ruff format --check src/ tests/ && mypy src/ && python -m pytest tests/ -q`
- [x] 7.4 Verify all new config variables documented in `.env.example`
- [x] 7.5 Update spec delta files if any requirement details changed during implementation
