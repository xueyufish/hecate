# ADR-009: OpenAI-Compatible API + Management API Dual Track

> **Status**: Accepted

## Context

Hecate needs to support both OpenAI-compatible interfaces (for seamless integration with existing tools and frameworks) and its own management API (for Agent/Workflow/Session CRUD). The design needed to determine path conventions and compatibility boundaries.

## Decision

Maintain **four API surfaces**:

- **OpenAI-compatible** at `/v1/` — Strictly follows OpenAI API spec. No extended field names. This is the highest-priority compatibility surface.
- **Management API** at `/api/` — RESTful CRUD for all Hecate entities. Follows REST conventions with unified error format.
- **MCP Server** at `/mcp` — Streamable HTTP transport (see [ADR-012](012-mcp-streamable-http.md)). Exposes Hecate agents as MCP-compliant tool providers for external consumption. Separately toggled via `MCP_SERVER_ENABLED`.
- **A2A Server** at `/.well-known/agent.json` + task endpoint — Agent Card discovery and A2A task lifecycle for cross-framework agent communication (see [ADR-011](011-a2a-protocol-adoption.md)). Enables external platforms to discover and invoke Hecate agents as A2A-compliant services.

Authentication is unified across all surfaces: `Authorization: Bearer <token>` (API Key or JWT).

## Rationale

OpenAI compatibility is the most important integration surface — it allows Hecate to work as a drop-in replacement for any tool that calls the OpenAI API. Keeping the `/v1/` path clean (no custom extensions) ensures maximum compatibility.

The management API handles everything OpenAI's spec doesn't cover: agent configuration, workflow management, knowledge base operations, skill loading, prompt versioning, and session administration.

## Consequences

- `/v1/` paths must never include Hecate-specific parameters — use `/api/` instead
- The unified error format (`{error: {code, message, details}}`) applies to both REST surfaces
- Streaming responses follow OpenAI's SSE format on `/v1/` paths
- The MCP `/mcp` endpoint follows MCP protocol semantics (JSON-RPC over Streamable HTTP), not REST conventions
- The A2A `/.well-known/agent.json` endpoint follows A2A protocol semantics (Agent Card discovery + task lifecycle), not REST conventions
