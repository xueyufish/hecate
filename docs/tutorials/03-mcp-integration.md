# Tutorial: MCP Tool Integration

> Documentation in progress. This tutorial will cover connecting external MCP servers as tool providers and exposing Hecate as an MCP server.

## What you will build

A Hecate agent that calls external tools via the Model Context Protocol, and a Hecate MCP server that other MCP-compatible clients can consume.

## Prerequisites

- Hecate running locally (see [Quickstart](../getting-started/quickstart.md))
- An MCP server to connect to (or use a built-in tool for testing)

## Steps (outline)

1. Enable the MCP client in Hecate configuration
2. Register an external MCP server endpoint
3. Attach MCP-discovered tools to an agent
4. Chat with the agent — it will invoke MCP tools as needed
5. (Optional) Enable `MCP_SERVER_ENABLED=true` to expose Hecate as an MCP server

## Further reading

- [Tool Platform Design](../design/tool-platform-design.md)
- [Ecosystem Design](../design/ecosystem-design.md)