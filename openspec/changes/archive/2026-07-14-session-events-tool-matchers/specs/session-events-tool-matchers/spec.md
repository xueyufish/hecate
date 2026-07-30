## ADDED Requirements — 新增需求

### Requirement: Session lifecycle event hooks — 需求：会话生命周期事件钩子
The system SHALL provide 4 session lifecycle hook ABCs: `SessionStartHook` (fires when a session begins or resumes), `SessionEndHook` (fires when a session ends), `UserPromptSubmitHook` (fires when a user submits a prompt, before LLM processing), and `PreCompactHook` (fires before context compaction). Each hook follows the existing GuardrailHook pattern with async execution.

系统应提供 4 个会话生命周期钩子 ABC：`SessionStartHook`（会话开始或恢复时触发）、`SessionEndHook`（会话结束时触发）、`UserPromptSubmitHook`（用户提交提示时、LLM 处理前触发）和 `PreCompactHook`（上下文压缩前触发）。每个钩子遵循现有的 GuardrailHook 模式，支持异步执行。

#### Scenario: SessionStart fires on new session — 场景：新会话时 SessionStart 触发
- **WHEN** a new agent session is created
- **THEN** all registered SessionStartHook instances are called with session metadata

- **当**创建新的代理会话
- **则**所有已注册的 SessionStartHook 实例被调用，附带会话元数据

#### Scenario: SessionStart context injection — 场景：SessionStart 上下文注入
- **WHEN** a SessionStartHook returns text in its result
- **THEN** the text is injected into the LLM context for the first turn

- **当** SessionStartHook 在其结果中返回文本
- **则**该文本被注入到第一轮 LLM 上下文中

#### Scenario: UserPromptSubmit can block — 场景：UserPromptSubmit 可以阻止
- **WHEN** a UserPromptSubmitHook returns BLOCK
- **THEN** the prompt is rejected and an error message is returned to the user

- **当** UserPromptSubmitHook 返回 BLOCK
- **则**提示被拒绝，并向用户返回错误消息

#### Scenario: SessionEnd cleanup — 场景：SessionEnd 清理
- **WHEN** a session ends (user disconnects, timeout, or explicit close)
- **THEN** all registered SessionEndHook instances are called for cleanup

- **当**会话结束（用户断开、超时或显式关闭）
- **则**调用所有已注册的 SessionEndHook 实例进行清理

#### Scenario: PreCompact before compression — 场景：压缩前 PreCompact
- **WHEN** context compaction is about to occur
- **THEN** PreCompactHook instances are called, allowing backup or context preservation

- **当**上下文压缩即将发生时
- **则**调用 PreCompactHook 实例，允许备份或上下文保存

### Requirement: Tool name matcher for tool hooks — 需求：工具钩子的工具名称匹配器
The system SHALL support optional tool name matchers on `PreToolHook` and `PostToolHook`. Matchers use the syntax: exact name (`web_search`), pipe-separated (`Edit|Write`), regex (`mcp__.*`), or None/`*` for all tools. Only hooks whose matcher matches the current tool name are executed.

系统应在 `PreToolHook` 和 `PostToolHook` 上支持可选的工具名称匹配器。匹配器使用以下语法：精确名称（`web_search`）、管道分隔（`Edit|Write`）、正则表达式（`mcp__.*`）或 None/`*` 表示所有工具。仅执行匹配器匹配当前工具名称的钩子。

#### Scenario: Exact match — 场景：精确匹配
- **WHEN** a PreToolHook has matcher `"web_search"` and the tool name is `web_search`
- **THEN** the hook executes

- **当** PreToolHook 的匹配器为 `"web_search"` 且工具名称为 `web_search`
- **则**钩子执行

#### Scenario: Exact match does not fire for different tool — 场景：精确匹配对不同的工具不触发
- **WHEN** a PreToolHook has matcher `"web_search"` and the tool name is `bash`
- **THEN** the hook is skipped

- **当** PreToolHook 的匹配器为 `"web_search"` 且工具名称为 `bash`
- **则**钩子被跳过

#### Scenario: Pipe-separated match — 场景：管道分隔匹配
- **WHEN** a PostToolHook has matcher `"Edit|Write"` and the tool name is `Edit`
- **THEN** the hook executes

- **当** PostToolHook 的匹配器为 `"Edit|Write"` 且工具名称为 `Edit`
- **则**钩子执行

#### Scenario: Regex match — 场景：正则表达式匹配
- **WHEN** a PreToolHook has matcher `"mcp__github__.*"` and the tool name is `mcp__github__create_issue`
- **THEN** the hook executes (regex match)

- **当** PreToolHook 的匹配器为 `"mcp__github__.*"` 且工具名称为 `mcp__github__create_issue`
- **则**钩子执行（正则匹配）

#### Scenario: No matcher matches all — 场景：无匹配器时匹配所有
- **WHEN** a PreToolHook has no matcher (None) and any tool is called
- **THEN** the hook executes (backward compatible)

- **当** PreToolHook 没有匹配器（None）且任何工具被调用
- **则**钩子执行（向后兼容）

### Requirement: Shell command hooks — 需求：Shell 命令钩子
The system SHALL provide a `ShellCommandHook` implementation that executes shell commands. The hook receives event data as JSON on stdin. Exit code 0 means proceed, exit code 2 means block (stderr fed back). For SessionStart and UserPromptSubmit events, stdout is injected into LLM context. Shell execution is gated by `HOOK_SHELL_ENABLED` setting (default False).

系统应提供执行 shell 命令的 `ShellCommandHook` 实现。钩子通过 stdin 接收 JSON 格式的事件数据。退出码 0 表示继续，退出码 2 表示阻止（反馈 stderr）。对于 SessionStart 和 UserPromptSubmit 事件，stdout 被注入到 LLM 上下文中。Shell 执行受 `HOOK_SHELL_ENABLED` 设置控制（默认 False）。

#### Scenario: Shell hook proceeds (exit 0) — 场景：Shell 钩子继续（退出码 0）
- **WHEN** a ShellCommandHook runs and the command exits with code 0
- **THEN** the hook returns ALLOW and execution continues

- **当** ShellCommandHook 运行且命令以代码 0 退出
- **则**钩子返回 ALLOW，继续执行

#### Scenario: Shell hook blocks (exit 2) — 场景：Shell 钩子阻止（退出码 2）
- **WHEN** a ShellCommandHook runs and the command exits with code 2
- **THEN** the hook returns BLOCK with stderr as reason

- **当** ShellCommandHook 运行且命令以代码 2 退出
- **则**钩子返回 BLOCK，并将 stderr 作为原因

#### Scenario: Shell hook timeout — 场景：Shell 钩子超时
- **WHEN** a shell command exceeds the configured timeout (default 30s)
- **THEN** the process is killed and the hook returns ALLOW with a warning log

- **当** shell 命令超过配置的超时时间（默认 30 秒）
- **则**进程被终止，钩子返回 ALLOW 并记录警告

#### Scenario: Shell disabled — 场景：Shell 禁用
- **WHEN** `HOOK_SHELL_ENABLED` is False
- **THEN** no ShellCommandHook instances are created or executed

- **当** `HOOK_SHELL_ENABLED` 为 False
- **则**不会创建或执行任何 ShellCommandHook 实例

### Requirement: Hook configuration via JSON — 需求：通过 JSON 配置钩子
The system SHALL support declaring hooks via JSON configuration loaded from DB-backed `HookConfigModel`. Each hook config specifies: event name, optional matcher, shell command, and timeout. Configurations are scoped to workspace or agent level.

系统应支持通过从数据库支持的 `HookConfigModel` 加载的 JSON 配置声明钩子。每个钩子配置指定：事件名称、可选匹配器、shell 命令和超时时间。配置限定到工作空间或代理级别。

#### Scenario: Create hook config — 场景：创建钩子配置
- **WHEN** a client creates a hook config via API with event, matcher, and command
- **THEN** the hook is stored and activated on the next matching event

- **当**客户端通过 API 使用事件、匹配器和命令创建钩子配置
- **则**钩子被存储，并在下一个匹配事件时激活

#### Scenario: Workspace-level hook — 场景：工作空间级钩子
- **WHEN** a hook config has `agent_id=None`
- **THEN** the hook applies to all agents in the workspace

- **当**钩子配置的 `agent_id=None`
- **则**钩子应用于工作空间中的所有代理

#### Scenario: Agent-level hook — 场景：代理级钩子
- **WHEN** a hook config has a specific `agent_id`
- **THEN** the hook applies only to that agent

- **当**钩子配置具有特定的 `agent_id`
- **则**钩子仅应用于该代理

### Requirement: REST API for hook management — 需求：钩子管理 REST API
The system SHALL expose REST API endpoints for hook configuration CRUD.

系统应公开用于钩子配置 CRUD 的 REST API 端点。

#### Scenario: List hooks — 场景：列出钩子
- **WHEN** a client requests `GET /api/hooks`
- **THEN** the system returns all hook configurations, optionally filtered by agent_id or event

- **当**客户端请求 `GET /api/hooks`
- **则**系统返回所有钩子配置，可按 agent_id 或事件过滤

#### Scenario: Create hook — 场景：创建钩子
- **WHEN** a client requests `POST /api/hooks` with hook config data
- **THEN** the system creates the hook config and returns 201

- **当**客户端使用钩子配置数据请求 `POST /api/hooks`
- **则**系统创建钩子配置并返回 201

#### Scenario: Delete hook — 场景：删除钩子
- **WHEN** a client requests `DELETE /api/hooks/{id}`
- **THEN** the system removes the hook config and returns 204

- **当**客户端请求 `DELETE /api/hooks/{id}`
- **则**系统移除钩子配置并返回 204
