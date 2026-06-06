## Why

`_ProductionEnginePort.tool_execute()` and `ConversationService._execute_tools_with_evidence()` both return mock strings (`"Executed {name} with args {args}"`). The entire tool execution chain is disconnected — LLM decides to call tools, ToolWorker parses them, but no real tool logic runs. Agents cannot perform any actual work (search the web, read files, execute code). This is the last critical gap for P1 completion.

## What Changes

- Add a `ToolRegistry` service that routes `tool_execute()` calls by source type (builtin / custom / mcp)
- Add a `BuiltInToolExecutor` that registers and executes 5 built-in tools: `web_search`, `read_file`, `write_file`, `list_files`, `execute_code`
- Wire `ToolRegistry` into `_ProductionEnginePort.tool_execute()`, replacing the mock stub
- Add configurable search provider support (Tavily / Serper / DuckDuckGo) via environment variables
- Seed built-in tool definitions to the `tools` DB table on startup (`source="builtin"`, `workspace_id=00000000`)
- `execute_code` uses existing `SandboxExecutor` + `SandboxPool` infrastructure
- ToolRegistry interface supports all three source types; only `builtin` path is implemented in this change
- `custom` and `mcp` routing paths are reserved (raise `NotImplementedError`) for future P2/P3 implementation
- `ConversationService._execute_tools_with_evidence()` stub is left unchanged — it will be replaced by the unified engine migration

## Capabilities

### New Capabilities
- `tool-registry`: Central tool routing service that maps tool names to executors by source type (builtin/custom/mcp)
- `builtin-tools`: Five built-in tools (web_search, read_file, write_file, list_files, execute_code) with configurable search provider and sandbox-based code execution

### Modified Capabilities
- `engine-ports`: `tool_execute()` gains a concrete implementation via ToolRegistry instead of mock stub

## Impact

- **New files**: `src/hecate/services/tool/registry.py`, `src/hecate/services/tool/builtin.py`, `src/hecate/services/tool/search/` (search provider adapters)
- **Modified files**: `src/hecate/services/orchestration/engine_port_adapter.py` (wire ToolRegistry), `src/hecate/core/config.py` (add search provider config)
- **New tests**: `tests/test_services/test_tool/` (registry + builtin tools + search providers)
- **New dependencies**: `tavily-python` or `duckduckgo-search` (optional, based on provider choice) in `[dev]` or `[tools]` group
- **DB migration**: Seed built-in tool definitions to `tools` table via startup event (not Alembic migration — data is reproducible from code)
- **No breaking changes**: Existing API behavior preserved; tools that previously returned mock strings now return real results
