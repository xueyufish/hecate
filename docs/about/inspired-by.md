# Inspired by

Hecate builds on the shoulders of many proven projects and open standards. This page credits each one and explains what Hecate borrowed.

## Execution model

- **[Google Pregel: A System for Large-Scale Graph Processing](https://research.google.com/pubs/large-scale-graph-computation-at-google/)** — the original Bulk Synchronous Parallel (BSP) graph computation paper. Hecate's superstep loop is named after and informed by it.
- **[LangGraph](https://github.com/langchain-ai/langgraph)** — pioneered channel/checkpoint/Pregel patterns in the Python Agent ecosystem. Hecate borrows the conceptual model (channel types, checkpoint persistence, interrupt/resume) but re-implements the runtime from scratch with zero external framework dependencies.

## Protocols

- **[Model Context Protocol (MCP)](https://modelcontextprotocol.io/)** — Anthropic's open protocol for agent-to-tool integration. Hecate ships a native MCP client (consume external tools) and MCP server (expose Hecate as a tool provider) using Streamable HTTP transport.
- **[Agent-to-Agent (A2A) Protocol](https://a2a-protocol.org/)** — Linux Foundation v1.0 GA standard for cross-framework agent communication. Hecate agents are A2A-discoverable via Agent Cards (`/.well-known/agent.json`) and invokable via the A2A task lifecycle.

## Infrastructure

- **[FastAPI](https://fastapi.tiangolo.com/)** — Sebastian Ramirez's modern async web framework. Hecate's API layer follows its conventions: Pydantic v2 schemas, dependency injection, automatic OpenAPI generation, and async request handling.
- **[Pydantic](https://docs.pydantic.dev/)** — data validation and serialization (v2). Powers every schema, model, and API contract in Hecate.
- **[SQLAlchemy](https://www.sqlalchemy.org/)** — async ORM (2.0). Backs all 47 data models with `workspace_id`-based tenant isolation across 35 tenant-scoped models.
- **[LiteLLM](https://github.com/BerriAI/litellm)** — unified LLM provider interface. Powers Hecate's model-agnostic routing across 100+ providers (OpenAI, Anthropic, DeepSeek, Qwen, GLM, Ollama, and more).
- **[Temporal](https://temporal.io/)** — durable workflow execution patterns inform Hecate's checkpoint + interrupt design and the optional Temporal-based conflict resolution for distributed execution.

## Process

Hecate's OpenSpec-driven development workflow is inspired by:

- **[Python PEPs](https://peps.python.org/)** — Python Enhancement Proposals
- **[Kubernetes KEPs](https://github.com/kubernetes/enhancements)** — Kubernetes Enhancement Proposals
- **[Rust RFCs](https://github.com/rust-lang/rfcs)** — Rust's RFC process

Like these projects, Hecate tracks every feature through a structured proposal → design → specs → implementation → archive lifecycle. These process documents live in `openspec/` and are contributor-facing, not user-facing.