# How-to Guides

Task-oriented recipes for specific problems. Each guide is self-contained — jump to the one you need.

## Configuration

- **Configure LLM providers** — set up OpenAI, Anthropic, DeepSeek, Qwen, GLM, Ollama, or any LiteLLM-supported provider.
- **Enable MCP Server** — expose Hecate as a tool provider via `MCP_SERVER_ENABLED=true`.
- **Enable A2A Server** — let external frameworks discover and invoke Hecate agents via `A2A_SERVER_ENABLED=true`.
- **Configure SSO / SCIM** — wire up SAML, OAuth2, or SCIM for enterprise identity management.

## Operations

- **Deploy to production** — Docker Compose, Kubernetes, or bare metal setup.
- **Configure backup and recovery** — scheduled backups, verification, and restore.
- **Monitor with OpenTelemetry** — tracing, metrics, and structured logging.
- **Scale horizontally** — Redis session state store, multi-replica deployment.

More guides are in progress.