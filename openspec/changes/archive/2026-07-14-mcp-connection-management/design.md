## Context

Hecate's MCP infrastructure has `HecateMCPClient` (single connection) and `MCPClientManager` (dict of named clients). Neither has pooling, reconnection, health checks, or lifecycle tracking. Claude Code's production issues (SSE drops, hanging Promises, manual `/mcp` reconnect) prove this is unacceptable for enterprise use.

**Research basis**: Analyzed 14 platforms. Key findings:
- AgentArts: two-step probe (TCP → SDK handshake), structured error codes, asset lifecycle binding
- Bedrock: lazy connection on first call, session reuse, shared gateway + identity-layer tenant isolation
- OpenClaw: health monitoring (60s), auto-reconnect, error isolation, session-level MCP runtime cache
- AgentScope: stateful/stateless client modes, execution_timeout, tool caching
- IBM watsonx: HTTP client pooling (max_connections=10), exponential backoff with jitter
- No platform does per-workspace connection pool isolation — all use shared pool + policy-layer isolation

## Goals / Non-Goals

**Goals:**
- Connection pooling (HTTP: min/max, stdio: single)
- Lazy connection + session reuse (Bedrock pattern)
- Two-step probe with structured error codes (AgentArts pattern)
- Auto-reconnection with exponential backoff
- Per-request timeout
- Health checks + circuit breaker
- Tool discovery caching with TTL
- MCP server registry (capability discovery)
- REST API + frontend status panel
- Multi-tenant isolation via PluginModel.workspace_id (shared pool, registration-layer isolation)

**Non-Goals:**
- Per-workspace connection pool isolation (no platform does this)
- Plugin signing/security (P5 5.13)
- MCP Gateway / protocol translation (5.4a, future)
- MCP Streamable HTTP transport upgrade (5.4b, future)
- Backward compatibility with existing MCPClientManager API (development stage)

## Decisions

### Decision 1: Lazy connection + session reuse (Bedrock pattern)

**Choice**: MCP server registered on plugin enable (no connection), connection created on first tool call, session reused for subsequent calls.

**Rationale**: Bedrock's pattern is the most efficient — avoids connecting to MCP servers that are never used. AgentArts and Dify connect on install/enable, which wastes resources for unused servers.

### Decision 2: Two-step probe with structured error codes (AgentArts pattern)

**Choice**: Before creating MCP connection, perform TCP reachability check, then SDK protocol handshake. Return structured error codes for diagnosis.

**Rationale**: AgentArts's error code system (02401173 DNS / 02401161 timeout / 02401162 port / 02401163 404 / 02401164 WAF / 02401150 SSL) enables precise diagnosis. Claude Code's generic "MCP server offline" messages are useless for debugging.

### Decision 3: Shared connection pool + registration-layer isolation (Bedrock pattern)

**Choice**: Single connection pool keyed by server name. Multi-tenant isolation enforced at PluginModel.workspace_id — only the workspace that owns an MCP server plugin can trigger its connection.

**Rationale**: No enterprise platform (Bedrock, AgentArts, Palantir, watsonx) does per-workspace connection pool isolation. All use shared infrastructure + policy/identity-layer isolation. Dify's per-workspace daemon process is resource-heavy and unnecessary.

### Decision 4: No backward compatibility

**Choice**: Rewrite MCPClientManager API directly.

**Rationale**: Development stage, no external consumers of the internal API. Maintaining compatibility adds complexity without value.

### Decision 5: Circuit breaker with 3-state model

**Choice**: Closed (normal) → Open (5 consecutive failures, reject all) → Half-open (30s probe) → Closed (probe success) / Open (probe failure).

**Rationale**: Standard circuit breaker pattern. Prevents cascade failures from unhealthy MCP servers. OpenClaw uses similar 3-strike detection.

## Risks / Trade-offs

- **[Pool exhaustion]** — All connections in use, new requests wait. Mitigation: borrow_timeout (default 5s), requests fail fast instead of blocking indefinitely.

- **[Health check overhead]** — Periodic `list_tools` ping adds load to MCP servers. Mitigation: configurable interval (default 30s), ping is read-only with no side effects.

- **[Reconnection storm]** — Multiple MCP servers reconnecting simultaneously. Mitigation: jitter added to exponential backoff, max 5 retries per server.

- **[Tool cache staleness]** — Cached tool list may be outdated if MCP server adds/removes tools. Mitigation: TTL (default 5 min) + manual sync API endpoint + single-flight refresh.
