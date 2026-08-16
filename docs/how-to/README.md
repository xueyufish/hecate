# How-to Guides

Task-oriented recipes for specific problems. Each guide is self-contained — jump to the one you need.

## Quick recipes

- **[Cookbook](cookbook.md)** — copy-paste recipes for the most common patterns: web-search agent, knowledge-grounded Q&A, model fallback, streaming, multi-agent handoff, MCP integration, local models, and more.

## Development

- **[Develop custom extensions](develop-extensions.md)** — implement custom CheckpointStore, GuardrailHook, SchedulerStrategy, and other extension points. Three concrete code examples with wiring and testing patterns.
- **[Use OpenSpec for change management](openspec-workflow.md)** — the eight-phase change lifecycle (explore → worktree → propose → apply → push → merge → archive), file lifecycle, git history, and the two safe archive workflows.

## Configuration

- **[Configure LLM providers](configure-llm-providers.md)** — set up OpenAI, Anthropic, DeepSeek, Qwen, GLM, Ollama, or any LiteLLM-supported provider via env vars or the database-backed provider registry.
- **[Configure budget and cost tracking](configure-budget.md)** — set per-workspace / per-agent budgets, degradation profiles, alerts, and respond to 429 budgets.
- **[Set up webhooks](set-up-webhooks.md)** — receive events from GitHub, Slack, or custom services; verify signatures; bind to workflows; handle retries and dead-letter queue.
- **[Enable MCP Server](enable-mcp-server.md)** — expose Hecate agents, knowledge bases, and tools as MCP primitives so Claude Desktop, Cursor, or any MCP client can invoke them.
- **[Enable A2A Server](enable-a2a-server.md)** — expose Hecate via the Agent-to-Agent protocol so LangGraph, CrewAI, AutoGen, and custom agents can discover and invoke your agents.
- **[Configure SSO and SCIM](configure-sso-scim.md)** — wire up OIDC, SAML, or LDAP for sign-in and SCIM v2 for automated user and group provisioning.

## Operations

- **[Deploy to production](deploy-production.md)** — Docker Compose, blue-green zero-downtime, Kubernetes, horizontal scaling, and backup/restore with PITR.
- **[Configure backup and recovery](deploy-production.md#backup-and-recovery)** — scheduled backups, verification, and restore.
- **[Monitor with OpenTelemetry and Prometheus](monitor-opentelemetry.md)** — distributed tracing, Prometheus metrics, structured logging, Kubernetes-style health probes, and trace inspection.
- **[Scale horizontally](deploy-production.md#horizontal-scaling)** — Redis session state store, multi-replica deployment.

## Versioning and lifecycle

- **[Version and roll back an agent or workflow](version-and-rollback-agent.md)** — save immutable versions, publish to production, roll back to any prior version, and audit every change.

## Security

- **[Security hardening](security-hardening.md)** — production security checklist: secrets, TLS, SSO, guardrails, DLP, tool permissions, sandbox isolation, audit/SIEM, database encryption, and monitoring.
- **[Configure tool permissions](configure-tool-permissions.md)** — workspace baselines, per-agent rules, and allow/deny lists for fine-grained tool access control.

## Memory and quality

- **[Manage agent memory](manage-agent-memory.md)** — configure L1 working-memory blocks, inspect and prune L3 user memories, manage L4 knowledge memories, and check L2 compression status.
- **[Evaluate an agent](../tutorials/08-agent-evaluation.md)** — build a test dataset, run evaluators, and detect regressions (tutorial).

## Observability and debugging

- **[Debug an agent run with execution replay](replay-debug-guide.md)** — open the Execution Replay tab, read trace segments, time-travel to any commit point, and use the replay REST API (8.20).

## Troubleshooting

- **[Troubleshooting guide](troubleshoot.md)** — common failure modes organized by problem domain: startup, database, LLM providers, tools/MCP, knowledge/RAG, authentication, workflows, and performance. Every error message sourced from the actual codebase.
