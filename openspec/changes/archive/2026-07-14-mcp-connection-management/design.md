## Context — 背景

Hecate's MCP infrastructure has `HecateMCPClient` (single connection) and `MCPClientManager` (dict of named clients). Neither has pooling, reconnection, health checks, or lifecycle tracking. Claude Code's production issues (SSE drops, hanging Promises, manual `/mcp` reconnect) prove this is unacceptable for enterprise use.

Hecate 的 MCP 基础设施有 `HecateMCPClient`（单连接）和 `MCPClientManager`（命名客户端字典）。两者都没有连接池、重连、健康检查或生命周期跟踪。Claude Code 的生产问题（SSE 断开、挂起的 Promise、手动 `/mcp` 重连）证明这对企业使用是不可接受的。

**Research basis — 研究基础**: Analyzed 14 platforms. Key findings — 关键发现:
- AgentArts: two-step probe (TCP → SDK handshake), structured error codes, asset lifecycle binding
- Bedrock: lazy connection on first call, session reuse, shared gateway + identity-layer tenant isolation
- OpenClaw: health monitoring (60s), auto-reconnect, error isolation, session-level MCP runtime cache
- AgentScope: stateful/stateless client modes, execution_timeout, tool caching
- IBM watsonx: HTTP client pooling (max_connections=10), exponential backoff with jitter
- No platform does per-workspace connection pool isolation — all use shared pool + policy-layer isolation

## Goals / Non-Goals — 目标/非目标

**Goals — 目标:**
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

**Non-Goals — 非目标:**
- Per-workspace connection pool isolation (no platform does this)
- Plugin signing/security (P5 5.13)
- MCP Gateway / protocol translation (5.4a, future)
- MCP Streamable HTTP transport upgrade (5.4b, future)
- Backward compatibility with existing MCPClientManager API (development stage)

## Decisions — 决策

### Decision 1: Lazy connection + session reuse (Bedrock pattern) — 懒连接 + 会话复用（Bedrock 模式）

**Choice — 选择**: MCP server registered on plugin enable (no connection), connection created on first tool call, session reused for subsequent calls.

**Rationale — 理由**: Bedrock's pattern is the most efficient — avoids connecting to MCP servers that are never used. AgentArts and Dify connect on install/enable, which wastes resources for unused servers.

Bedrock 的模式最高效 — 避免连接从未使用的 MCP 服务器。AgentArts 和 Dify 在安装/启用时连接，这在未使用的服务器上浪费资源。

### Decision 2: Two-step probe with structured error codes (AgentArts pattern) — 两步探测 + 结构化错误码（AgentArts 模式）

**Choice — 选择**: Before creating MCP connection, perform TCP reachability check, then SDK protocol handshake. Return structured error codes for diagnosis.

**Rationale — 理由**: AgentArts's error code system (02401173 DNS / 02401161 timeout / 02401162 port / 02401163 404 / 02401164 WAF / 02401150 SSL) enables precise diagnosis. Claude Code's generic "MCP server offline" messages are useless for debugging.

AgentArts 的错误码系统（02401173 DNS / 02401161 超时 / 02401162 端口 / 02401163 404 / 02401164 WAF / 02401150 SSL）实现精确诊断。Claude Code 的通用"MCP server offline"消息对调试无用。

### Decision 3: Shared connection pool + registration-layer isolation (Bedrock pattern) — 共享连接池 + 注册层隔离（Bedrock 模式）

**Choice — 选择**: Single connection pool keyed by server name. Multi-tenant isolation enforced at PluginModel.workspace_id — only the workspace that owns an MCP server plugin can trigger its connection.

**Rationale — 理由**: No enterprise platform (Bedrock, AgentArts, Palantir, watsonx) does per-workspace connection pool isolation. All use shared infrastructure + policy/identity-layer isolation. Dify's per-workspace daemon process is resource-heavy and unnecessary.

没有企业平台（Bedrock、AgentArts、Palantir、watsonx）做按工作空间的连接池隔离。都使用共享基础设施 + 策略/身份层隔离。Dify 的按工作空间守护进程资源密集且不必要。

### Decision 4: No backward compatibility — 无向后兼容

**Choice — 选择**: Rewrite MCPClientManager API directly.

**Rationale — 理由**: Development stage, no external consumers of the internal API. Maintaining compatibility adds complexity without value.

### Decision 5: Circuit breaker with 3-state model — 三状态模型断路器

**Choice — 选择**: Closed (normal) → Open (5 consecutive failures, reject all) → Half-open (30s probe) → Closed (probe success) / Open (probe failure).

**Rationale — 理由**: Standard circuit breaker pattern. Prevents cascade failures from unhealthy MCP servers. OpenClaw uses similar 3-strike detection.

## Risks / Trade-offs — 风险/权衡

- **[Pool exhaustion — 连接池耗尽]** — All connections in use, new requests wait. Mitigation: borrow_timeout (default 5s), requests fail fast instead of blocking indefinitely.
  缓解措施：borrow_timeout（默认 5s），请求快速失败而不是无限阻塞。

- **[Health check overhead — 健康检查开销]** — Periodic `list_tools` ping adds load to MCP servers. Mitigation: configurable interval (default 30s), ping is read-only with no side effects.
  缓解措施：可配置间隔（默认 30s），ping 是只读的，无副作用。

- **[Reconnection storm — 重连风暴]** — Multiple MCP servers reconnecting simultaneously. Mitigation: jitter added to exponential backoff, max 5 retries per server.
  缓解措施：在指数退避中添加抖动，每服务器最多 5 次重试。

- **[Tool cache staleness — 工具缓存过时]** — Cached tool list may be outdated if MCP server adds/removes tools. Mitigation: TTL (default 5 min) + manual sync API endpoint + single-flight refresh.
  缓解措施：TTL（默认 5 分钟）+ 手动同步 API 端点 + single-flight 刷新。
