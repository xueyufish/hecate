# How-to Guides

Task-oriented recipes for specific problems. Each guide is self-contained — jump to the one you need.

## Configuration

- **[Configure LLM providers](configure-llm-providers.md)** — set up OpenAI, Anthropic, DeepSeek, Qwen, GLM, Ollama, or any LiteLLM-supported provider via env vars or the database-backed provider registry.
- **[Enable MCP Server](enable-mcp-server.md)** — expose Hecate agents, knowledge bases, and tools as MCP primitives so Claude Desktop, Cursor, or any MCP client can invoke them.
- **[Enable A2A Server](enable-a2a-server.md)** — expose Hecate via the Agent-to-Agent protocol so LangGraph, CrewAI, AutoGen, and custom agents can discover and invoke your agents.
- **[Configure SSO and SCIM](configure-sso-scim.md)** — wire up OIDC, SAML, or LDAP for sign-in and SCIM v2 for automated user and group provisioning.

## Operations

- **[Deploy to production](deploy-production.md)** — Docker Compose, blue-green zero-downtime, Kubernetes, horizontal scaling, and backup/restore with PITR.
- **[Configure backup and recovery](deploy-production.md#backup-and-recovery)** — scheduled backups, verification, and restore.
- **[Monitor with OpenTelemetry and Prometheus](monitor-opentelemetry.md)** — distributed tracing, Prometheus metrics, structured logging, Kubernetes-style health probes, and trace inspection.
- **[Scale horizontally](deploy-production.md#horizontal-scaling)** — Redis session state store, multi-replica deployment.

More guides are in progress.