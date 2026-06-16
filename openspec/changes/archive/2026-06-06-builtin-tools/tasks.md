## 1. Search Provider Abstraction

- [x] 1.1 Create `src/hecate/services/tool/search/__init__.py` with `SearchProvider` ABC: `search(query: str, max_results: int) -> list[dict]`
- [x] 1.2 Create `src/hecate/services/tool/search/duckduckgo.py` — DuckDuckGo provider (default, no API key required) using `duckduckgo-search` package
- [x] 1.3 Create `src/hecate/services/tool/search/tavily.py` — Tavily provider using `tavily-python` package
- [x] 1.4 Create `src/hecate/services/tool/search/serper.py` — Serper provider using HTTP API
- [x] 1.5 Create `src/hecate/services/tool/search/factory.py` — `create_search_provider()` reads `SEARCH_PROVIDER` + `SEARCH_API_KEY` env vars, returns correct provider instance
- [x] 1.6 Add `duckduckgo-search` to optional `[tools]` dependency group in `pyproject.toml`; add `tavily-python` to `[tools]`

## 2. BuiltInToolExecutor

- [x] 2.1 Create `src/hecate/services/tool/__init__.py` (empty, package marker)
- [x] 2.2 Create `src/hecate/services/tool/builtin.py` with `BuiltInToolExecutor` class
- [x] 2.3 Implement `web_search` tool: accept `query` + `max_results`, delegate to `SearchProvider`, return list of `{title, url, snippet}`
- [x] 2.4 Implement `read_file` tool: accept `path`, resolve against `WORKSPACE_ROOT`, sanitize path, read and return file contents
- [x] 2.5 Implement `write_file` tool: accept `path` + `content`, resolve against `WORKSPACE_ROOT`, sanitize path, create parent dirs, write file
- [x] 2.6 Implement `list_files` tool: accept optional `path`, resolve against `WORKSPACE_ROOT`, return directory listing
- [x] 2.7 Implement `execute_code` tool: accept `code`, delegate to `SandboxExecutor.execute()`, return `{stdout, stderr, exit_code, timed_out}`; handle Docker-unavailable gracefully
- [x] 2.8 Define `BUILTIN_TOOL_DEFINITIONS` dict mapping tool name → `{description, parameters (JSON Schema)}` for all 5 tools

## 3. ToolRegistry

- [x] 3.1 Create `src/hecate/services/tool/registry.py` with `ToolRegistry` class
- [x] 3.2 Implement `execute(name, args, context)` method: check builtin names set first, then query DB for non-builtin, route by source type
- [x] 3.3 Implement builtin routing: delegate to `BuiltInToolExecutor`
- [x] 3.4 Implement custom/mcp routing: raise `NotImplementedError`
- [x] 3.5 Implement `seed_builtin_tools(db)` function: upsert builtin tool definitions to `tools` table with `source="builtin"`, `workspace_id=00000000`
- [x] 3.6 Add `WORKSPACE_ROOT` and `SEARCH_PROVIDER` / `SEARCH_API_KEY` to `src/hecate/core/config.py` (pydantic-settings)

## 4. Wire into EnginePort

- [x] 4.1 Modify `src/hecate/services/orchestration/engine_port_adapter.py`: inject `ToolRegistry` into `_ProductionEnginePort.__init__`, replace stub `tool_execute()` with `self._tool_registry.execute(name, args, context)`
- [x] 4.2 Update `create_engine_port()` factory to accept and pass `ToolRegistry`
- [x] 4.3 Find and update all `create_engine_port()` call sites to pass registry instance

## 5. Startup Seed

- [x] 5.1 Add startup event in `src/hecate/main.py` (FastAPI `lifespan` or `@app.on_event("startup")`) that calls `seed_builtin_tools(db)`
- [x] 5.2 Verify `GET /api/tools?source=builtin` returns all 5 builtin tools after startup

## 6. Tests

- [x] 6.1 Create `tests/test_services/test_tool/__init__.py`
- [x] 6.2 Create `tests/test_services/test_tool/test_search_providers.py` — test DuckDuckGo provider (live or mocked), factory resolution
- [x] 6.3 Create `tests/test_services/test_tool/test_builtin_executor.py` — test each of the 5 tools with mock filesystem and mock sandbox
- [x] 6.4 Create `tests/test_services/test_tool/test_registry.py` — test routing by source type, builtin lookup, unknown tool error, NotImplementedError for custom/mcp
- [x] 6.5 Create `tests/test_services/test_tool/test_seed.py` — test seed function inserts builtin tools, handles duplicates (upsert)
- [x] 6.6 Test path traversal prevention in read_file, write_file, list_files
- [x] 6.7 Run `python -m pytest tests/test_services/test_tool/ -v` — all pass

## 7. Verification

- [x] 7.1 Run `ruff check src/hecate/services/tool/ tests/test_services/test_tool/`
- [x] 7.2 Run `ruff format --check src/hecate/services/tool/ tests/test_services/test_tool/`
- [x] 7.3 Run `mypy src/hecate/services/tool/ src/hecate/services/orchestration/engine_port_adapter.py`
- [x] 7.4 Run `python -m pytest tests/ -q` — no regressions
