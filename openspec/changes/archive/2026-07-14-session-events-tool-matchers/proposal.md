## Why — 为什么

Hecate 现有的 Guardrail Hooks（PreLLMHook、PostLLMHook、PreToolHook、PostToolHook）覆盖了 AI 层的 4 个拦截点，但缺少会话级事件和按工具定位。企业部署需要：(1) 会话生命周期钩子（在开始时初始化工作空间、在结束时清理、在提示提交时注入上下文），(2) 工具名称匹配器，使钩子只针对特定工具触发（例如，仅在 `Edit|Write` 后自动格式化，仅对 `mcp__github__.*` 进行审计日志），(3) 设置驱动的 shell 命令钩子，使运营人员无需编写 Python 类即可自动化工作流。对 14 个平台的研究显示，Claude Code 的钩子系统（14+ 事件、工具匹配器、shell/http 处理器）是确定性生命周期控制的行业基准。

## What Changes — 变更内容

- **4 个会话事件钩子**：`SessionStartHook`、`SessionEndHook`、`UserPromptSubmitHook`、`PreCompactHook`——遵循现有 GuardrailHook 模式的新 ABC。SessionStart 可注入上下文（stdout → LLM 上下文），UserPromptSubmit 可阻止提示。
- **工具匹配器**：现有的 `PreToolHook` 和 `PostToolHook` 获得可选的 `matcher` 字段。匹配器使用正则/精确/管道分隔模式（Claude Code 语法）。仅匹配的钩子执行；不匹配的钩子跳过。向后兼容——没有匹配器的钩子为所有工具触发。
- **ShellCommandHook**：具体的钩子实现，执行通过 JSON 设置配置的 shell 命令。在 stdin 上接收事件数据作为 JSON，使用退出码（0=继续，2=阻止），stdout 注入上下文。支持可配置超时。
- **钩子配置**：用于声明钩子的基于 JSON 的设置格式：`{event, matcher, command, timeout}`。在启动时加载，可热重载。支持按工作空间和按代理作用域。
- **REST API**：钩子配置的 CRUD（`GET/POST/PUT/DELETE /api/hooks`）。

## Capabilities — 能力

### 新能力

- `session-events-tool-matchers`：4 个会话事件钩子 ABC、现有工具钩子的工具名称匹配器、ShellCommandHook 实现、JSON 钩子配置、用于钩子管理的 REST API

### 变更的能力

- _（无——现有 Guardrail Hooks 通过可选的匹配器进行扩展，向后兼容）_

## Impact — 影响

- **新文件**：
  - `src/hecate/engine/session_hooks.py` — 4 个会话事件钩子 ABC + NoOp 默认实现
  - `src/hecate/engine/tool_matcher.py` — ToolMatcher 类（正则/精确/管道匹配）
  - `src/hecate/engine/shell_hook.py` — ShellCommandHook（shell 命令执行、stdin/stdout/退出码）
  - `src/hecate/models/hook_config.py` — HookConfigModel + Pydantic schemas
  - `src/hecate/api/management/hooks.py` — 用于钩子 CRUD 的 REST API
  - `tests/test_engine/test_session_hooks.py` — 会话钩子测试
  - `tests/test_engine/test_tool_matcher.py` — 匹配器测试
- **修改的文件**：
  - `src/hecate/engine/guardrail.py` — PreToolHook/PostToolHook 获得可选的 `matcher: str | None`
  - `src/hecate/engine/workers/llm_worker.py` — 触发 UserPromptSubmitHook 和 PreCompactHook
  - `src/hecate/engine/workers/tool_worker.py` — 在调用 PreToolHook/PostToolHook 前应用工具匹配器
  - `src/hecate/services/workflow/execution_service.py` — 触发 SessionStartHook/SessionEndHook
  - `src/hecate/core/config.py` — `HOOK_SHELL_ENABLED`、`HOOK_SHELL_TIMEOUT`
  - `src/hecate/main.py` — 注册钩子路由
- **依赖**：无新增
