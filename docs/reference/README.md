# Reference

Technical reference material — API endpoints, CLI commands, configuration options, and data formats.

## Contents

- **[Quick Reference](quick-reference.md)** — one-page cheat sheet: API surfaces, CLI commands, Docker ports, env vars, node types, memory levels, hooks, and more. Bookmark this.
- **[Data Models](data-models.md)** — all 67 ORM tables grouped by domain, key foreign-key relationships, and the `BaseModel` pattern (UUID PK, timestamps, soft delete).
- **[Deployment Architectures](deployment-architectures.md)** — reference topologies (single-host, blue-green, Kubernetes), component diagrams, sizing guidelines, and stateful vs. stateless scaling.
- **[CLI Reference](cli.md)** — the `hecate` and `hecate-migrate` commands with all subcommands and flags.
- **[Environment Variables](env-vars.md)** — every configuration variable, with defaults and descriptions.
- **[Graph DSL](graph-dsl.md)** — JSON Schema reference for workflow graph definitions: 9 node types, 4 channel types, edge forms, and validation rules.
- **[Extension Points](extension-points.md)** — the 11 core + 4 SPI engine extension points, abstract methods, and default implementations.
- **[Glossary](glossary.md)** — definitions for Hecate-specific terms and domain acronyms used across the documentation.
- **[REST API](rest-api.md)** — route map of the four API surfaces (OpenAI-compatible `/v1`, management `/api`, identity/federation, system endpoints) with links to the interactive Swagger UI.
- **[FAQ](faq.md)** — answers to the most common questions, grouped by topic.
- **[AGENTS.md convention](agents-md.md)** — the coding-agent instructions file: what it is, how Hecate uses it, and how to work on Hecate with Claude Code, Codex, Cursor, and other coding agents.
- **Interactive API docs** — Swagger UI at `/docs` (Swagger) or `/redoc` (ReDoc) when the server is running.
