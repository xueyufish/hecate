# Security Hardening

A checklist for hardening a Hecate deployment before exposing it to production traffic. Every item references the specific configuration or feature that implements it.

> This guide assumes you have Hecate running locally (see [Quickstart](../getting-started/quickstart.md)) and are preparing for production. For the architectural "why" behind Hecate's security design, see [Security Architecture](../design/security-architecture.md).

---

## 1. Secrets and credentials

### API keys

- [ ] **Rotate the dev API key.** The default `HECATE_API_KEYS=dev-key-change-me` must be replaced with a strong, random key before production.
- [ ] **Use database-backed API keys** instead of env-var keys for production. Create keys via `POST /api/api-keys` with `scope=WORKSPACE` (not `SYSTEM`) for per-application isolation.
- [ ] **Set expiry on API keys.** Use the `expires_at` field and review `last_used_at` periodically to revoke stale keys.

```bash
# Generate a strong key
openssl rand -hex 32
```

### JWT secret

- [ ] **Set `JWT_SECRET`** to a cryptographically random value (≥ 32 bytes). Without it, the token service derives a default from existing API keys, which is weaker than an explicit secret.

```bash
# Generate a JWT secret
openssl rand -base64 48
```

### LLM provider keys

- [ ] **Prefer DB-registered providers** over env-var keys. DB keys are Fernet-encrypted at rest; env-var keys are plaintext in the environment.
- [ ] **Rotate provider keys** on a schedule. Update via `PUT /api/model-providers/{id}` without restarting the server.

---

## 2. Network and transport

### TLS

- [ ] **Terminate TLS** at a reverse proxy (nginx, Caddy, ALB) in front of Hecate. Hecate itself runs HTTP; TLS is the proxy's responsibility.
- [ ] **Redirect HTTP → HTTPS** at the proxy level.
- [ ] **Set `SAMESITE` cookies** if using session-based auth — configure at the proxy or application level.

### Network isolation

- [ ] **Do not expose internal ports.** Only port `8000` (Hecate) should be externally reachable. PostgreSQL (5432), Qdrant (6333), MinIO (9000), and Temporal (7233) must be on a private network.
- [ ] **Restrict MinIO console** (port 9001) to an internal network or VPN. The console provides administrative access to object storage.
- [ ] **Firewall the Docker network.** Use Docker Compose network isolation or Kubernetes `NetworkPolicy` to prevent pod-to-pod access except on required ports.

```yaml
# docker-compose.yml — only expose Hecate externally
services:
  hecate:
    ports: ["8000:8000"]
  postgres:
    # NO ports mapping — only accessible within the compose network
    expose: ["5432"]
```

---

## 3. Authentication and access control

### SSO

- [ ] **Enable SSO** for all human users. Use OIDC (recommended), SAML, or LDAP. See [Configure SSO and SCIM](configure-sso-scim.md).
- [ ] **Disable password-based login** in production by not registering local users. SSO-authenticated users get JIT-provisioned `UserModel` rows with `sso_id`.
- [ ] **Enable SCIM v2** for automated deprovisioning. When an admin disables a user in the IdP, SCIM automatically sets `active=false` in Hecate.

### Rate limiting

- [ ] **Set `RATE_LIMIT_RPM`** to an appropriate value for your traffic. The default is 60 requests/minute per key.
- [ ] **Use per-workspace quotas** via `POST /api/quotas` to prevent one tenant from exhausting platform capacity.

### Workspace isolation

- [ ] **Create separate workspaces** per team or customer. Data isolation is enforced by `workspace_id` on every data model — see [Multi-Tenancy](../concepts/multi-tenancy.md).
- [ ] **Review workspace membership** regularly. A user's role (`admin`/`editor`/`viewer`) governs what they can do across accessible workspaces.

---

## 4. Guardrails and content security

### LLM Guard

- [ ] **Keep `LLM_GUARD_ENABLED=true`** (default). This enables prompt-injection screening on inputs and content filtering on outputs at the [engine hook layer](../concepts/guardrails.md).

### Data Loss Prevention (DLP)

- [ ] **Keep `DLP_ENABLED=true`** (default). The DLP engine scans every trust boundary — PreLLM, PostLLM, PostTool, MCP responses — for PII, secrets, and custom patterns.
- [ ] **Configure entity-specific policies**. Not every entity needs the same action:
  - Credit card numbers → `BLOCK` (never emit)
  - Email addresses → `MASK` (redact in logs, preserve in app)
  - API keys → `BLOCK` on output, `AUDIT` on detection
- [ ] **Test your DLP config** before enabling in production. Use the DLP scan test endpoint to verify patterns match without affecting live traffic.

See [DLP concept](../concepts/dlp.md) for the full recognizer and policy model.

### Tool permissions

- [ ] **Set workspace-level deny rules** for dangerous tools. This is a security baseline that cannot be overridden by agent config:

```bash
curl -X POST http://localhost:8000/api/tool-policies \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "rule_action": "deny",
    "tool_pattern": "execute_code",
    "description": "No code execution in production workspace"
  }'
```

- [ ] **Require human approval** for `HIGH` and `CRITICAL` risk tools via `PreToolHook` with `interrupt()`. See [Guardrails and Hooks](../concepts/guardrails.md#risk-levels-and-approval-scopes).
- [ ] **Restrict MCP tool servers** to approved providers. Audit which MCP servers are registered via `GET /api/mcp/servers`.

---

## 5. Sandbox and runtime isolation

### Docker sandbox

- [ ] **Keep sandbox enabled for `execute_code`**. The Docker `SandboxPool` isolates untrusted code execution. Verify the pool is configured:

```dotenv
SANDBOX_POOL_ENABLED=true
SANDBOX_MAX_CONTAINERS=10
SANDBOX_TIMEOUT_SECONDS=30
```

- [ ] **Limit sandbox network access.** Configure Docker network rules to prevent sandboxed code from reaching internal services (PostgreSQL, Redis, Qdrant).

### Container security

- [ ] **Run Hecate as a non-root user** in the Docker container. The default Dockerfile should specify `USER hecate` or equivalent.
- [ ] **Read-only filesystem** where possible. Mount only the data directory as writable; everything else read-only.
- [ ] **Resource limits** on the Hecate container:

```yaml
services:
  hecate:
    deploy:
      resources:
        limits:
          cpus: "4"
          memory: 8G
```

---

## 6. Audit trail and SIEM

### Audit logging

- [ ] **Verify audit logging is active.** Every `Pre/PostLLM/Tool` hook event writes a structured `SecurityEvent` to the SIEM pipeline. Check via:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/audit?limit=10"
```

### SIEM export

- [ ] **Configure at least one SIEM exporter** for compliance. Hecate supports three formats: Webhook (Slack/PagerDuty), Syslog (RFC 5424), and OCSF (Open Cyberalytics Schema). See [Guardrails — SIEM pipeline](../concepts/guardrails.md#from-hook-events-to-the-siem-pipeline).
- [ ] **Forward security findings** to your incident response workflow. `GET /api/security/findings` returns long-lived policy-violation findings.

### Log retention

- [ ] **Set log retention** per your compliance requirements. Configure Docker log rotation or your log shipper (Fluent Bit, Vector) to enforce retention.
- [ ] **Encrypt log storage** if logs contain PII (they will, despite DLP masking — structured logs include `session_id` and `agent_id`).

---

## 7. Database security

### Connection encryption

- [ ] **Use TLS for database connections.** Append `?sslmode=require` to `DATABASE_URL` for PostgreSQL:

```dotenv
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db?sslmode=require
```

### Backup encryption

- [ ] **Encrypt backups at rest.** The backup pipeline (`pg_dump`) produces plaintext SQL. Use MinIO server-side encryption or filesystem-level encryption for backup storage.
- [ ] **Test backup restoration** regularly. See [Backup and Restore runbook](../operations/backup-restore.md).

### Credential rotation

- [ ] **Rotate the PostgreSQL password** periodically. Update `DATABASE_URL` and restart, or use a secrets manager with dynamic credentials.

---

## 8. Monitoring and alerting

### Health probes

- [ ] **Wire up Kubernetes probes** (or Docker healthchecks) to `/health/live`, `/health/ready`, `/health/startup`. See [Health Checks](../operations/health-checks.md).
- [ ] **Alert on probe failures.** `/health/ready` returns 503 when DB/Redis/Qdrant are unreachable — this is your early warning for infrastructure issues.

### Agent health

- [ ] **Configure per-agent health thresholds**:

```dotenv
AGENT_HEALTH_ERROR_RATE_WARNING=0.05
AGENT_HEALTH_ERROR_RATE_CRITICAL=0.15
AGENT_HEALTH_LATENCY_CRITICAL_MS=30000
```

- [ ] **Set up alert rules** for error rate spikes and cost anomalies via `POST /api/alerts/rules`. See [Observability](../concepts/observability.md).

### DLP findings

- [ ] **Monitor DLP findings** for policy violations. A spike in `BLOCK` actions may indicate a prompt-injection attack or a misconfigured agent leaking data.
- [ ] **Review `AUDIT`-action findings** periodically — they indicate sensitive data was detected but not blocked, which may warrant a policy tightening.

---

## Pre-production checklist summary

| Category | Critical items |
|----------|---------------|
| Secrets | Rotate dev key, set `JWT_SECRET`, encrypt provider keys |
| Network | TLS termination, no internal ports exposed, firewall |
| Auth | SSO enabled, SCIM deprovisioning, rate limiting |
| Guardrails | LLM Guard on, DLP configured, tool deny rules |
| Sandbox | Docker isolation for `execute_code`, resource limits |
| Audit | SIEM exporter configured, findings monitored |
| Database | TLS connections, encrypted backups |
| Monitoring | Health probes, agent health thresholds, alerting |

---

## Further reading

- [Security Architecture](../design/security-architecture.md) — full L2 breakdown: hooks, PII, LLM Guard, RBAC, audit
- [Guardrails and Hooks](../concepts/guardrails.md) — the four engine-level hook types
- [Data Loss Prevention (DLP)](../concepts/dlp.md) — recognizer, policy, and action model
- [Authentication and Identity](../concepts/auth-identity.md) — API keys, JWT, SSO, SCIM
- [Configure SSO and SCIM](configure-sso-scim.md) — step-by-step identity provider wiring
- [Configure tool permissions](configure-tool-permissions.md) — workspace and agent-level rules
- [ADR-008: Security via Hooks](../design/adr/008-security-via-hooks.md) — why hooks live in the engine
- [ADR-018: Zero Trust Identity](../design/adr/018-zero-trust-identity-architecture.md) — planned two-tier token model
