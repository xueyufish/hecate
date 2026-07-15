## Why

Hecate's existing Guardrail Hooks (PreLLMHook, PostLLMHook, PreToolHook, PostToolHook) cover AI-interception at 4 points, but lack session-level events and per-tool targeting. Enterprise deployments need: (1) session lifecycle hooks (initialize workspace on start, cleanup on end, inject context on prompt submit), (2) tool name matchers so hooks only fire for specific tools (e.g., auto-format only after `Edit|Write`, audit-log only for `mcp__github__.*`), and (3) settings-driven shell command hooks so operators can automate workflows without writing Python classes. Research across 14 platforms shows Claude Code's hook system (14+ events, tool matchers, shell/http handlers) is the industry benchmark for deterministic lifecycle control.

## What Changes

- **4 Session Event Hooks**: `SessionStartHook`, `SessionEndHook`, `UserPromptSubmitHook`, `PreCompactHook` — new ABCs following the existing GuardrailHook pattern. SessionStart can inject context (stdout → LLM context), UserPromptSubmit can block prompts.
- **Tool Matcher**: Existing `PreToolHook` and `PostToolHook` gain an optional `matcher` field. Matcher uses regex/exact/pipe-separated patterns (Claude Code syntax). Only matching hooks execute; non-matching hooks skip. Backward compatible — hooks without matcher fire on all tools.
- **ShellCommandHook**: A concrete hook implementation that executes shell commands configured via JSON settings. Receives event data as JSON on stdin, uses exit codes (0=proceed, 2=block), stdout injection for context. Supports configurable timeout.
- **Hook Configuration**: JSON-based settings format for declaring hooks: `{event, matcher, command, timeout}`. Loaded at startup, hot-reloadable. Per-workspace and per-agent scope.
- **REST API**: CRUD for hook configurations (`GET/POST/PUT/DELETE /api/hooks`).

## Capabilities

### New Capabilities

- `session-events-tool-matchers`: 4 session event hook ABCs, tool name matcher for existing tool hooks, ShellCommandHook implementation, JSON hook configuration, REST API for hook management

### Modified Capabilities

- _(none — existing Guardrail Hooks are extended with optional matcher, backward compatible)_

## Impact

- **New files**:
  - `src/hecate/engine/session_hooks.py` — 4 session event hook ABCs + NoOp defaults
  - `src/hecate/engine/tool_matcher.py` — ToolMatcher class (regex/exact/pipe matching)
  - `src/hecate/engine/shell_hook.py` — ShellCommandHook (shell command execution, stdin/stdout/exit code)
  - `src/hecate/models/hook_config.py` — HookConfigModel + Pydantic schemas
  - `src/hecate/api/management/hooks.py` — REST API for hook CRUD
  - `tests/test_engine/test_session_hooks.py` — session hook tests
  - `tests/test_engine/test_tool_matcher.py` — matcher tests
- **Modified files**:
  - `src/hecate/engine/guardrail.py` — PreToolHook/PostToolHook gain optional `matcher: str | None`
  - `src/hecate/engine/workers/llm_worker.py` — fire UserPromptSubmitHook and PreCompactHook
  - `src/hecate/engine/workers/tool_worker.py` — apply tool matcher before calling PreToolHook/PostToolHook
  - `src/hecate/services/workflow/execution_service.py` — fire SessionStartHook/SessionEndHook
  - `src/hecate/core/config.py` — `HOOK_SHELL_ENABLED`, `HOOK_SHELL_TIMEOUT`
  - `src/hecate/main.py` — register hooks router
- **Dependencies**: None new
