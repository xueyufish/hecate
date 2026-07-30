## 1. Session Event Hook ABCs — 会话事件钩子 ABC

- [x] 1.1 创建 `src/hecate/engine/session_hooks.py` — `SessionStartHook` ABC（`on_session_start(session_id, agent_id, source) → HookResult`）、`SessionEndHook` ABC（`on_session_end(session_id, agent_id, reason) → HookResult`）、`UserPromptSubmitHook` ABC（`on_user_prompt_submit(session_id, prompt) → HookResult`）、`PreCompactHook` ABC（`on_pre_compact(session_id, trigger) → HookResult`）。`HookResult` dataclass：`action`（ALLOW/BLOCK/INJECT）、`context_text`（用于注入）、`reason`。每个都有 NoOp 默认实现。

## 2. Tool Matcher — 工具匹配器

- [x] 2.1 创建 `src/hecate/engine/tool_matcher.py` — `ToolMatcher` 类：`match(tool_name, matcher_pattern) → bool`。模式评估：纯字母数字 + `|` → 精确/管道分隔；包含正则特殊字符 → `re.match`；None/empty/`*` → True（匹配所有）。为性能预编译正则模式。

## 3. Shell Command Hook — Shell 命令钩子

- [x] 3.1 创建 `src/hecate/engine/shell_hook.py` — `ShellCommandHook` 类，实现所有钩子接口。`__init__(command, timeout, event_type)`。通过 `asyncio.create_subprocess_shell()` 执行，在 stdin 传递事件 JSON，读取 stdout/stderr，映射退出码（0=ALLOW，2=BLOCK）。超时处理包含进程终止。受 `HOOK_SHELL_ENABLED` 控制。

## 4. Extend Guardrail Hooks with Matcher — 使用匹配器扩展 Guardrail Hooks

- [x] 4.1 更新 `src/hecate/engine/guardrail.py` — `PreToolHook.on_pre_tool_call()` 获得可选的 `matcher: str | None = None` 类属性。`PostToolHook.on_post_tool_call()` 相同。NoOp 变体获得 `matcher = None`。
- [x] 4.2 更新 `src/hecate/engine/workers/tool_worker.py` — 在调用 PreToolHook/PostToolHook 前，检查 `ToolMatcher.match(tool_name, hook.matcher)`。如果匹配器不匹配则跳过钩子。

## 5. Session Hook Integration Points — 会话钩子集成点

- [x] 5.1 更新 `src/hecate/services/workflow/execution_service.py` — 在会话创建时触发 `SessionStartHook`，会话结束时触发 `SessionEndHook`。将 SessionStart 的 context_text 注入到首轮消息中。
- [x] 5.2 更新 `src/hecate/engine/workers/llm_worker.py` — 在处理用户消息前触发 `UserPromptSubmitHook`。在上下文压缩前触发 `PreCompactHook`。UserPromptSubmit 返回 BLOCK → 返回错误消息。

## 6. Data Model + Config — 数据模型 + 配置

- [x] 6.1 创建 `src/hecate/models/hook_config.py` — `HookConfigModel`（id、workspace_id、agent_id 可空、event str、matcher str 可空、command str、timeout int、enabled bool）。Pydantic Create/Read schemas。
- [x] 6.2 向 `src/hecate/core/config.py` 添加设置：`HOOK_SHELL_ENABLED: bool = False`、`HOOK_SHELL_TIMEOUT: int = 30`。

## 7. REST API — REST API

- [x] 7.1 创建 `src/hecate/api/management/hooks.py` — 路由前缀 `/api/hooks`：`GET /`（列表，按 agent_id/event 过滤）、`POST /`（创建）、`DELETE /{id}`（删除）。
- [x] 7.2 在 `src/hecate/main.py` 中注册 `hooks_router`。

## 8. Tests — 测试

- [x] 8.1 测试 `ToolMatcher` — 精确匹配、管道分隔、正则、None/empty/`*`、大小写敏感性。
- [x] 8.2 测试 `ShellCommandHook` — exit 0（ALLOW）、exit 2（BLOCK + 原因）、超时（终止 + ALLOW + 警告）、禁用（跳过）。
- [x] 8.3 测试会话钩子 — SessionStart 触发 + 上下文注入、UserPromptSubmit BLOCK、SessionEnd 清理、PreCompact 触发。
- [x] 8.4 测试工具匹配器集成 — 带匹配器的 PreToolHook 仅对匹配的工具触发、PostToolHook 相同、向后兼容（无匹配器 = 所有工具）。
- [x] 8.5 测试 REST API — 列表/创建/删除钩子。

## 9. Verification — 验证

- [x] 9.1 运行 `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 错误
- [x] 9.2 运行 `mypy src/` — 0 错误
- [x] 9.3 运行 `python -m pytest tests/test_engine/test_session_hooks.py tests/test_engine/test_tool_matcher.py -q` — 全部通过
