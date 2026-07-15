## 1. Session Event Hook ABCs

- [x] 1.1 Create `src/hecate/engine/session_hooks.py` — `SessionStartHook` ABC (`on_session_start(session_id, agent_id, source) → HookResult`), `SessionEndHook` ABC (`on_session_end(session_id, agent_id, reason) → HookResult`), `UserPromptSubmitHook` ABC (`on_user_prompt_submit(session_id, prompt) → HookResult`), `PreCompactHook` ABC (`on_pre_compact(session_id, trigger) → HookResult`). `HookResult` dataclass: `action` (ALLOW/BLOCK/INJECT), `context_text` (for injection), `reason`. NoOp defaults for each.

## 2. Tool Matcher

- [x] 2.1 Create `src/hecate/engine/tool_matcher.py` — `ToolMatcher` class: `match(tool_name, matcher_pattern) → bool`. Pattern evaluation: plain alphanumerics + `|` → exact/pipe-separated; contains regex special chars → `re.match`; None/empty/`*` → True (match all). Pre-compile regex patterns for performance.

## 3. Shell Command Hook

- [x] 3.1 Create `src/hecate/engine/shell_hook.py` — `ShellCommandHook` class implementing all hook interfaces. `__init__(command, timeout, event_type)`. Execute via `asyncio.create_subprocess_shell()`, pass event JSON on stdin, read stdout/stderr, map exit codes (0=ALLOW, 2=BLOCK). Timeout handling with process kill. Gated by `HOOK_SHELL_ENABLED`.

## 4. Extend Guardrail Hooks with Matcher

- [x] 4.1 Update `src/hecate/engine/guardrail.py` — `PreToolHook.on_pre_tool_call()` gains optional `matcher: str | None = None` class attribute. `PostToolHook.on_post_tool_call()` same. NoOp variants get `matcher = None`.
- [x] 4.2 Update `src/hecate/engine/workers/tool_worker.py` — before calling PreToolHook/PostToolHook, check `ToolMatcher.match(tool_name, hook.matcher)`. Skip hook if matcher doesn't match.

## 5. Session Hook Integration Points

- [x] 5.1 Update `src/hecate/services/workflow/execution_service.py` — fire `SessionStartHook` at session creation, `SessionEndHook` at session end. Inject context_text from SessionStart into first-turn messages.
- [x] 5.2 Update `src/hecate/engine/workers/llm_worker.py` — fire `UserPromptSubmitHook` before processing user messages. Fire `PreCompactHook` before context compression. BLOCK on UserPromptSubmit → return error message.

## 6. Data Model + Config

- [x] 6.1 Create `src/hecate/models/hook_config.py` — `HookConfigModel` (id, workspace_id, agent_id nullable, event str, matcher str nullable, command str, timeout int, enabled bool). Pydantic Create/Read schemas.
- [x] 6.2 Add settings to `src/hecate/core/config.py`: `HOOK_SHELL_ENABLED: bool = False`, `HOOK_SHELL_TIMEOUT: int = 30`.

## 7. REST API

- [x] 7.1 Create `src/hecate/api/management/hooks.py` — router prefix `/api/hooks`: `GET /` (list, filter by agent_id/event), `POST /` (create), `DELETE /{id}` (delete).
- [x] 7.2 Register `hooks_router` in `src/hecate/main.py`.

## 8. Tests

- [x] 8.1 Test `ToolMatcher` — exact match, pipe-separated, regex, None/empty/`*`, case sensitivity.
- [x] 8.2 Test `ShellCommandHook` — exit 0 (ALLOW), exit 2 (BLOCK + reason), timeout (kill + ALLOW + warning), disabled (skip).
- [x] 8.3 Test session hooks — SessionStart fires + context injection, UserPromptSubmit BLOCK, SessionEnd cleanup, PreCompact fires.
- [x] 8.4 Test tool matcher integration — PreToolHook with matcher only fires for matching tools, PostToolHook same, backward compat (no matcher = all tools).
- [x] 8.5 Test REST API — list/create/delete hooks.

## 9. Verification

- [x] 9.1 Run `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 errors
- [x] 9.2 Run `mypy src/` — 0 errors
- [x] 9.3 Run `python -m pytest tests/test_engine/test_session_hooks.py tests/test_engine/test_tool_matcher.py -q` — all pass
