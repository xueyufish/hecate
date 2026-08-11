# Troubleshooting

Common failure modes, their error signatures, and how to fix them. Organized by problem domain — jump to the section that matches your symptom. Every error message below is extracted from the actual codebase, not invented.

> For conceptual questions ("what is a superstep?", "which mode should I choose?"), see the [FAQ](../reference/faq.md). For production operations runbooks, see [Operations](../operations/).

---

## Installation and startup

### `Unsupported database dialect: ...`

**Source**: `core/database.py`

Hecate's async engine supports PostgreSQL (`postgresql+asyncpg`) and SQLite (`sqlite+aiosqlite`). MySQL requires the `[mysql]` extra:

```bash
uv pip install -e ".[mysql]"
```

The MySQL error message tells you exactly this: *"MySQL support requires aiomysql. Install with: pip install hecate[mysql]"*.

### Port already in use (8000)

The default `uvicorn hecate:app --reload` uses port 8000. If another process holds it:

```bash
lsof -i :8000     # find the PID
kill <PID>
# or run on a different port:
uvicorn hecate.main:app --port 8001
```

### Docker Compose services won't start

Check that ports 5432 (Postgres), 6333 (Qdrant), 9000/9001 (MinIO), and 7233 (Temporal) are free on the host. Verify all services are healthy:

```bash
docker compose -f docker/docker-compose.yml ps
docker compose -f docker/docker-compose.yml logs postgres
```

---

## Database and migrations

### `alembic upgrade head` fails

Most migration failures are one of:

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Connection refused` | Postgres not running or wrong `DATABASE_URL` | `docker compose up -d postgres`, verify `DATABASE_URL` in `.env` |
| `Target database is not up to date` | Multiple Alembic processes raced | Run `alembic upgrade head` once; use `hecate-migrate` in init containers |
| `Can't locate revision identified by '<hash>'` | Branch ahead of DB history | `alembic history` to find the right revision; `alembic stamp <rev>` to realign |

### `pg_dump failed` / `createdb failed`

**Source**: `services/backup/pg_backup.py`, `services/backup/restore.py`

The backup/restore pipeline shells out to `pg_dump` and `createdb`. These errors mean the system `pg_dump` binary is missing, the wrong version, or the DB user lacks permissions:

```bash
# Verify pg_dump is installed and matches PG major version
pg_dump --version              # must match your Postgres major (e.g., 16)
which pg_dump                  # ensure it's on PATH inside the container
```

In Docker, use the official `postgres:16` image as the backup init container so `pg_dump` version matches.

---

## LLM and model providers

### `AuthenticationError` / `401 Unauthorized` from LLM provider

The API key env var is missing or wrong. Hecate passes keys to LiteLLM, which passes them to the provider. Check:

```bash
# Is the key set in the environment?
env | grep -i OPENAI_API_KEY
env | grep -i ANTHROPIC_API_KEY

# Test the key directly:
curl https://api.openai.com/v1/models -H "Authorization: Bearer $OPENAI_API_KEY"
```

For DB-registered providers, verify the key is set via `GET /api/model-providers` — the response shows `status: configured` (not `unconfigured`).

### All fallback models failed

**Source**: `services/llm/service.py` — `_try_fallback()`

The primary model and every model in the `fallback_models` list raised. The log shows each attempt:

```
Trying fallback model: gpt-4o-mini
Fallback model gpt-4o-mini also failed: AuthenticationError ...
```

Fix: check whether the failure is auth (keys), rate limits (upgrade tier), or network (firewall). If all providers share the same root cause, fallback won't help — fix the root cause.

### Circuit breaker tripped — requests skip straight to fallback

**Source**: `services/llm/circuit_breaker.py`

When a provider prefix (e.g., `deepseek/`) exceeds the failure threshold, the breaker opens. Requests to that provider return immediately without waiting for timeout. This is **expected behavior** — the fix is to wait for the cooldown period or address the upstream outage.

Check which providers are tripped via traces — look for spans with `circuit_breaker=open` in the attributes.

### `NoCapableModelError` from routing

**Source**: `services/llm/routing.py`

The `ModelRouter` found no model in the candidate pool that meets the routing constraints. Either expand the candidate list or relax the constraints (e.g., lower the minimum capability score for `CAPABILITY` strategy).

---

## Tools and MCP

### `Tool '{name}' not found`

**Source**: `services/tool/registry.py:85`

The tool name doesn't match any built-in tool or any row in the `tools` table for the current workspace. Verify:

```bash
# List all tools visible to this workspace
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/tools?source=mcp"
```

Common causes: the tool belongs to a different workspace, the tool was soft-deleted, or the MCP server hasn't been synced yet.

### `Custom tool execution not yet implemented`

**Source**: `services/tool/registry.py:92`

The `custom` tool source is a roadmap feature. The `ToolModel.source` field accepts `"custom"` but the registry can't execute it yet. Use MCP tools or built-in tools instead. See [Tools, MCP, and A2A](../concepts/tools-and-mcp.md).

### `MCPClientManager not configured in ToolRegistry`

**Source**: `services/tool/registry.py:95`

An MCP tool was invoked, but the registry has no MCP client manager. This means the MCP subsystem wasn't initialized at startup — check `MCP_SERVER_ENABLED` and the MCP server registration via `GET /api/mcp/servers`.

### `MCP tool '{name}' has no mcp_server configured`

**Source**: `services/tool/registry.py:99`

The tool's `source` is `"mcp"` but `mcp_server` is `NULL` in the database. Re-register the tool with the correct `mcp_server` and `mcp_tool_name` fields, or re-sync from the MCP server.

### `Path traversal detected: ... resolves outside workspace`

**Source**: `services/tool/builtin.py:169`

The `read_file`, `write_file`, or `list_files` tool was asked to access a path that escapes the agent's workspace root (e.g., `../../etc/passwd`). This is a security guard, not a bug. Fix the agent's prompt or the tool arguments to stay within the workspace.

### `Container ... timed out after {timeout}s`

**Source**: `services/sandbox/executor.py:247`

The Docker sandbox for `execute_code` didn't finish in time. Increase the timeout in the sandbox config, or optimize the code being executed. Check that the Docker daemon is running and the sandbox pool has available containers.

---

## Knowledge and RAG

### Embedding model not loaded / mock embeddings in use

**Source**: `services/rag/embedding.py`

When FlagEmbedding is not installed, `EmbeddingService` falls back to deterministic hash-based mock vectors. Retrieval will technically work but relevance will be poor. Install the dependency:

```bash
uv pip install -e ".[rag]"
```

Verify by checking logs for any message about "mock" or "FlagEmbedding not installed" on startup.

### Qdrant connection refused

The vector store can't reach Qdrant. Verify:

```bash
docker compose -f docker/docker-compose.yml ps qdrant
curl http://localhost:6333/healthz
```

Check `VECTOR_STORE_TYPE` in `.env` — if set to `qdrant`, the Qdrant URL must be reachable. For local development without Docker, switch to Chroma: `VECTOR_STORE_TYPE=chroma`.

### `File not found: {file_path}` during document upload

**Source**: `services/rag/parser.py:53`

The file was referenced but not found at the given path. For uploaded files, ensure MinIO is running (`docker compose ps minio`) and the upload completed successfully.

---

## Authentication

### `Invalid API key` / `Missing Authorization header`

**Source**: `services/mcp/auth.py`, `auth/api_key_provider.py`

The request didn't include a valid `Authorization: Bearer <token>` header. For API keys, the token must start with `hcat_`. For JWTs, use the `access_token` returned by `/auth/login` or SSO callback.

```bash
# Correct format:
curl -H "Authorization: Bearer hcat_your_key_here" http://localhost:8000/api/agents
```

### SSO callback returns error

Common causes for OIDC/SAML callback failures:

| Symptom | Cause | Fix |
|---------|-------|-----|
| `redirect_uri_mismatch` | IdP redirect URI doesn't match Hecate's callback URL | Set `https://hecate.example.com/auth/sso/oidc/callback` in the IdP |
| `invalid_client` | Wrong Client ID / Secret | Verify `SSO_OIDC_CLIENT_ID` and `SSO_OIDC_CLIENT_SECRET` in `.env` |
| Discovery URL unreachable | IdP blocks the Hecate container's subnet | `docker compose exec hecate curl -fsS $SSO_OIDC_DISCOVERY_URL` |
| `Algorithm not allowed: ... Only ES256 is accepted` | A2A AgentCard signed with wrong algorithm | Re-sign with ES256 — see `a2a/signing.py` |

---

## Workflow execution

### `Channel type '{name}' not registered`

**Source**: `engine/channel.py:156`

The graph DSL references a channel type that the `ChannelManager` doesn't recognize. Valid types: `last_value`, `topic`, `persistent_topic` (deprecated), `accumulator`. Check for typos in the `type` field of your channel declarations.

### Unreachable node warnings

**Source**: `engine/compiler.py` — `_detect_unreachable()`

The compiler logs a WARNING for nodes that can't be reached from the entry point via BFS traversal. The graph still compiles and runs — the unreachable nodes just never execute. Check your edges to ensure every node is reachable from `entry`.

### `fan-out` without reachable `merge`

**Source**: `engine/compiler.py:261-262`

The compiler rejects a graph where a `fan-out` node has no downstream `merge` node. Every `fan-out` must have a corresponding `merge` to collect the parallel branches. See the [Graph DSL Reference](../reference/graph-dsl.md#node-types).

---

## Performance

### Requests are slow / timing out

1. **Check traces** — `GET /api/traces?limit=10` and look for spans with high `duration_ms`.
2. **LLM latency** — the biggest contributor is usually the LLM call itself. Try a faster model or use `LATENCY` routing strategy.
3. **Circuit breaker** — if a provider is tripped, requests fall back immediately (good) but the fallback may be slower.
4. **DB queries** — check PostgreSQL slow query log. Missing indexes on `workspace_id` filters are the usual culprit.
5. **Sandbox startup** — `execute_code` spawns Docker containers; the pool reuses them but cold starts are slow. Increase pool size.

### Memory usage growing over time

The in-memory `MetricsCollector` and `MetricsStore` buffer metrics in process memory. Under high load, set `MAX_METRICS_BUFFER_SIZE` to cap it, or switch to `METRICS_STORE_TYPE=timescale` to offload to TimescaleDB. See [Observability](../concepts/observability.md#metrics).

---

## Further reading

- [FAQ](../reference/faq.md) — conceptual questions ("what is X?")
- [Health Checks](../operations/health-checks.md) — probe semantics and alerting
- [Log Analysis](../operations/log-analysis.md) — trace/log correlation for debugging
- [Backup and Restore](../operations/backup-restore.md) — backup failures and conflict resolution
- [Rollback Runbook](../operations/rollback.md) — four rollback paths for bad deploys
- [Environment Variables](../reference/env-vars.md) — every config variable with defaults
