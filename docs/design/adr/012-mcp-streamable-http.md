# ADR-012: MCP Streamable HTTP Transport Upgrade

> **Status**: Accepted — **updated 2026-08-22**: implementation migrated to the MCP 2026-07-28 specification (stateless core, no `initialize` handshake, header-based routing) via change `mcp-streamable-http` (feature 5.4b close-out). The transport remains Streamable HTTP on a single `/mcp` endpoint; the protocol-era semantics below that reference the 2025-03-26 revision (`Mcp-Session-Id`, initialize handshake, GET SSE subscription stream) are superseded by the 2026-07-28 stateless core.
> **Date**: 2026-06-29

## Context

The MCP (Model Context Protocol) specification evolved significantly since Hecate's initial implementation. The 2025-03-26 specification introduced Streamable HTTP, which replaces the dual-endpoint architecture (separate HTTP + SSE endpoints) with a single `/mcp` endpoint that uses standard HTTP POST/GET with optional SSE upgrade for long-running operations.

This change has major operational implications:
- **No sticky sessions required**: Standard load balancers (round-robin) work without session affinity
- **Stateless operation**: Servers can respond immediately for fast operations or upgrade to SSE for streaming
- **Industry adoption**: MCP hit 97 million monthly SDK downloads in March 2026, up from ~2 million at launch. Every major provider (Anthropic, OpenAI, Google DeepMind, Microsoft, AWS) has adopted the new spec.

## Decision

Upgrade Hecate's MCP implementation to the 2025-03-26 Streamable HTTP specification:

1. **Single endpoint**: Replace dual HTTP+SSE endpoints with single `/mcp` endpoint
2. **POST/GET semantics**: POST for client→server messages, GET for SSE stream subscription
3. **SSE upgrade**: Server responds immediately for fast operations, upgrades to SSE for long-running tasks
4. **Stateless mode**: Support stateless operation for horizontal scaling without sticky sessions

## Rationale

- **Operational simplicity**: Eliminates the need for sticky sessions, which is a blocker for horizontal scaling
- **Load balancer compatibility**: Works with any standard load balancer (round-robin, least-connections)
- **Industry standard**: All major MCP SDKs and servers have adopted the new spec
- **Performance**: Immediate response for fast operations, SSE upgrade only when needed
- **Backward compatibility**: The new spec is backward-compatible with existing MCP tool definitions

## Consequences

- MCP server configuration changes from dual-endpoint to single `/mcp` endpoint
- Load balancer configuration simplifies (no sticky sessions needed)
- Horizontal scaling of MCP server becomes trivial
- Existing MCP tool definitions remain unchanged
- Client SDKs need to be updated to use the new transport
