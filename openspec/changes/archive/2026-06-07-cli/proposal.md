## Why

Hecate exposes 68 REST API endpoints across 12 resource domains, but all interaction currently requires direct HTTP calls (curl, httpx, or custom code). A command-line interface is needed to provide a developer-friendly surface for the platform — enabling rapid agent lifecycle management, interactive chat sessions with streaming, knowledge base operations, and full CRUD for all resources. This is the foundation for future SDK (1.2.1) and NL2Agent (1.1.7) capabilities.

## What Changes

- Add a `hecate` CLI command via `typer` + `rich` framework, registered as `[project.scripts]` entry point in `pyproject.toml`
- Create `src/hecate/cli/` module with nested subcommand structure covering all 68 API endpoints across: config, auth, agent, session, chat, kb, tool, skill, workflow, prompt, memory, template, conversation, model-provider
- Implement configuration management via `~/.hecate/config.toml` with named profiles (base_url, api_key)
- Support dual authentication: API Key (direct) and JWT (via `hecate auth login` with automatic token refresh)
- Implement interactive chat mode (`hecate chat interactive`) with SSE streaming support via `httpx.stream`
- Default table output via `rich`, with `--json` flag for machine-readable output
- All commands communicate with Hecate REST API via `httpx` (already a dependency)

## Capabilities

### New Capabilities
- `cli`: Command-line interface for the Hecate Agent Platform — covers CLI framework, config management, auth, all resource CRUD commands, and interactive chat with streaming

### Modified Capabilities
- `core-infrastructure`: Add CLI-related settings (default profile, output format) to Settings class

## Impact

- **New code**: `src/hecate/cli/` directory (~15-20 files)
- **Dependencies**: `typer>=0.15.0`, `rich>=13.0.0` (both new), `tomli>=2.0` for TOML config reading (Python 3.12 has `tomllib` in stdlib, no new dep needed)
- **pyproject.toml**: Add `typer` and `rich` to main dependencies; add `[project.scripts]` entry point
- **No changes to existing API or service code** — CLI is a pure client-side addition
- **No changes to engine layer** — CLI only talks to REST API
