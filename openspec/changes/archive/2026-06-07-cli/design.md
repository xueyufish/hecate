## Context

Hecate exposes 68 REST API endpoints (16 routers, 12 resource domains) but has no CLI. All interaction requires direct HTTP calls. The platform needs a developer-friendly CLI for agent lifecycle management, interactive chat, knowledge base operations, and full CRUD for all resources.

Current state:
- All endpoints use Bearer token auth (`verify_api_key` — accepts API Key or JWT)
- Responses follow consistent JSON format (`{"items": [...], "total": N}` for lists)
- Chat endpoint supports SSE streaming (`POST /v1/chat/completions` with `stream=true`)
- `httpx` is already a dependency (used in tests and MCP client)
- No `[project.scripts]` entry point exists in pyproject.toml

## Goals / Non-Goals

**Goals:**
- Provide `hecate` CLI covering all 68 API endpoints
- Interactive chat with SSE streaming
- Configuration management with named profiles
- Dual authentication (API Key + JWT with auto-refresh)
- Human-readable table output (default) + `--json` flag
- Zero changes to existing server code

**Non-Goals:**
- Independent `hecate-cli` package (CLI ships with main package)
- GUI / TUI beyond basic interactive chat
- Offline mode or local agent execution
- Shell completion scripts (can be added later via typer built-in support)
- Plugin system for CLI extensions

## Decisions

### D1: Framework — typer + rich

**Choice**: typer (with rich as typer's default renderer)

**Rationale**:
- typer is type-annotation driven — consistent with Hecate's FastAPI + Pydantic + SQLAlchemy 2.0 style
- Langflow, Prefect Cloud, and AutoGen Studio all use typer for their CLIs
- typer底层是click，需要时可以混用click原生API
-样板代码最少 — 68个端点需要大量子命令，`Annotated[type, Option()]` 比click装饰器简洁

**Alternatives**:
- click: 更成熟但样板多，Dify/CrewAI/LiteLLM使用
- cyclopts: Prefect 3.x刚迁移过去，还不够成熟
- argparse: 样板极多，不适合68个端点

### D2: Command structure — nested subcommands

**Choice**: `hecate <resource> <action>` nested structure

```
hecate agent list
hecate agent create --name "My Agent" --model gpt-4o
hecate chat interactive <agent_id>
hecate kb upload <kb_id> document.pdf
```

**Rationale**: 68 endpoints cannot be flat. Each API router maps to a CLI subcommand group.

### D3: Configuration — TOML file with profiles

**Choice**: `~/.hecate/config.toml` with named profiles

```toml
[default]
base_url = "http://localhost:8000"
api_key = "hec-xxxxx"
output = "table"

[profiles.staging]
base_url = "https://staging.hecate.io"
api_key = "hec-yyyyy"
```

**Rationale**: TOML is Python-native (stdlib `tomllib` since 3.11), supports profiles for multi-environment workflows, and is the standard for Python tooling (pyproject.toml, ruff, mypy).

**Alternatives**:
- Environment variables only: No profile support, poor UX for multi-env
- JSON: Less human-readable, no comments
- YAML: Requires additional dependency

### D4: Authentication — dual mode

**Choice**: Support both API Key (direct) and JWT (login + auto-refresh)

- `hecate config set api_key hec-xxx` — store API key directly
- `hecate auth login --email user@example.com` — obtain JWT, store refresh token, auto-refresh on expiry

**Rationale**: API Key is simplest for service accounts. JWT is needed for user-scoped operations (P3 multi-tenant). Both auth methods are already supported by `verify_api_key`.

### D5: HTTP client — httpx (sync)

**Choice**: Use `httpx` synchronous client for all CLI→API communication

**Rationale**: typer commands are synchronous. httpx is already a dependency. SSE streaming uses `httpx.stream("POST", ...)` with line-by-line parsing. No need for async in CLI context.

### D6: Output formatting — rich tables + --json

**Choice**: Default to rich tables, `--json` for machine-readable output

**Rationale**: All AI platform CLIs (LiteLLM, Langflow, Prefect Cloud) use rich for output. `--json` enables piping to `jq` or scripting.

### D7: Chat interactive — SSE streaming

**Choice**: `hecate chat interactive <agent_id>` opens REPL with streaming responses

**Implementation**: 
- Use `httpx.stream("POST", "/v1/chat/completions", json={..., "stream": True})` 
- Parse SSE events line-by-line (`data: {...}`)
- Print content tokens incrementally via `rich.console.Console.print`
- Support slash commands: `/clear`, `/exit`, `/history`

**Rationale**: Without streaming, interactive chat would wait 3-10 seconds then dump full response — unusable UX.

### D8: Module structure

```
src/hecate/cli/
├── __init__.py
├── main.py              # Root typer app, entry point
├── config.py            # Config loading, profile management
├── client.py            # HTTP client wrapper (httpx)
├── auth.py              # Login, token refresh, whoami
├── output.py            # Table/JSON formatting utilities
├── commands/
│   ├── __init__.py
│   ├── agent.py         # hecate agent ...
│   ├── session.py       # hecate session ...
│   ├── chat.py          # hecate chat send/interactive
│   ├── kb.py            # hecate kb ...
│   ├── tool.py          # hecate tool ...
│   ├── skill.py         # hecate skill ...
│   ├── workflow.py      # hecate workflow ...
│   ├── prompt.py        # hecate prompt ...
│   ├── memory.py        # hecate memory ...
│   ├── template.py      # hecate template ...
│   ├── conversation.py  # hecate conversation ...
│   ├── model.py         # hecate model list / hecate model providers ...
│   └── message.py       # hecate message citations
```

## Risks / Trade-offs

- **[Sync vs async]** CLI uses sync httpx. For parallel API calls (e.g., listing multiple resources), this is sequential. Acceptable trade-off — CLI is interactive, not batch.
- **[TOML in Python 3.12]** `tomllib` is read-only. For config write operations (`hecate config set`), we need to serialize TOML manually or add `tomli_w` dependency. Mitigation: implement minimal TOML writer (config is flat key-value, not complex nesting).
- **[JWT refresh race]** If JWT expires mid-session, CLI must refresh transparently. Mitigation: check expiry before each request, refresh proactively.
- **[68 commands = large CLI]** One CLI module per resource domain keeps each file manageable (~100-200 lines). Template pattern (create one, copy for others) ensures consistency.
