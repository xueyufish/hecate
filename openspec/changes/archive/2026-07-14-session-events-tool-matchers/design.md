## Context

Hecate has 4 Guardrail Hooks (PreLLMHook, PostLLMHook, PreToolHook, PostToolHook) that intercept at the AI layer. They are Python ABCs returning ALLOW/BLOCK/SANITIZE. What's missing: session lifecycle events (start/end/prompt/compact), per-tool targeting (all tool hooks fire for every tool), and settings-driven shell command hooks (require writing Python classes).

**Research basis** (14 platforms):
- Claude Code: 14+ hook events (PreToolUse, PostToolUse, SessionStart, SessionEnd, UserPromptSubmit, PreCompact, Stop, etc.), tool matchers (exact/pipe/regex), `if` field for argument filtering, 5 handler types (command/http/mcp_tool/prompt/agent), JSON settings config (3 levels), stdin JSON / exit code / stdout context injection
- AgentScope: 6 onion middleware positions (on_reply/on_reasoning/on_acting/on_model_call/on_compress_context/on_system_prompt), MiddlewareBase class, auto-detect implemented hooks, TracingMiddleware for OTel spans
- Salesforce: before_reasoning / after_reasoning deterministic blocks, Agent Script DSL mixing program logic with LLM instructions, available_when per-action gating
- Enterprise platforms (Bedrock/Google/IBM): Policy-based, not hook-based

## Goals / Non-Goals

**Goals:**
- 4 session event hook ABCs (SessionStart, SessionEnd, UserPromptSubmit, PreCompact)
- Tool name matcher on existing PreToolHook/PostToolHook (regex/exact/pipe)
- ShellCommandHook — settings-driven shell command execution
- JSON hook configuration with per-workspace/per-agent scope
- REST API for hook management
- Backward compatible — existing hooks without matcher work unchanged

**Non-Goals:**
- ReAct loop middleware (AgentScope onion pattern) — E3, deferred
- HTTP/MCP tool/prompt/agent handler types — Claude Code has 5 types; we start with shell only
- `if` field for argument-level filtering — deferred, tool name matching is sufficient for v1
- PostToolUseFailure, PostToolBatch, SubagentStart/Stop events — deferred
- Notification, PermissionRequest, CwdChanged events — Claude Code specific, not applicable

## Decisions

### Decision 1: Extend Guardrail Hooks, don't replace

**Choice**: Add `matcher` parameter to existing PreToolHook/PostToolHook. Add 4 new Session Hook ABCs alongside existing hooks.

**Rationale**: The existing 4 Guardrail Hooks work well and are tested. Replacing them risks regressions. Adding optional matcher is backward compatible — hooks without matcher fire on all tools (current behavior).

### Decision 2: Shell command hooks (Claude Code pattern)

**Choice**: ShellCommandHook executes shell commands configured via JSON. stdin receives event JSON, exit code 0=proceed, exit code 2=block, stdout=inject context (for SessionStart/UserPromptSubmit only).

**Rationale**: Claude Code's shell hook pattern is proven and developer-friendly. Enterprise operators can configure hooks (auto-format, lint, audit-log) without writing Python. Security: shell execution gated by `HOOK_SHELL_ENABLED` setting (default False), per-hook timeout.

### Decision 3: Matcher syntax (Claude Code compatible)

**Choice**: Matcher string evaluated as: plain alphanumerics + `|` → exact/pipe-separated; any regex special char → regex. Empty/None → match all.

Examples:
- `"web_search"` → exact match
- `"Edit|Write"` → matches either
- `"mcp__github__.*"` → regex match all GitHub MCP tools
- `None` or `"*"` → match all tools

**Rationale**: Claude Code's matcher syntax is simple and proven. We adopt it directly for familiarity.

### Decision 4: JSON settings configuration

**Choice**: Hooks configured via JSON in settings or DB-backed HookConfigModel:

```json
{
  "hooks": [
    {
      "event": "PostToolUse",
      "matcher": "Edit|Write",
      "command": "prettier --write $FILE_PATH",
      "timeout": 10
    },
    {
      "event": "SessionStart",
      "command": "echo 'Reminder: use Bun, not npm'",
      "timeout": 5
    }
  ]
}
```

**Rationale**: Claude Code uses JSON settings (3 levels: user/project/local). Hecate uses DB-backed model for per-workspace/per-agent scope, plus a global settings fallback.

## Risks / Trade-offs

- **[Shell execution security]** — ShellCommandHook executes arbitrary commands. Mitigation: `HOOK_SHELL_ENABLED` default False, per-hook timeout, audit logging of all hook executions.

- **[Performance]** — Matcher evaluation on every tool call adds overhead. Mitigation: pre-compile regex patterns at config load time, skip evaluation when matcher is None.

- **[Backward compatibility]** — Existing PreToolHook/PostToolHook implementations don't have matcher. Mitigation: matcher is optional; hooks without matcher match everything (current behavior).
