## ADDED Requirements

### Requirement: Session lifecycle event hooks
The system SHALL provide 4 session lifecycle hook ABCs: `SessionStartHook` (fires when a session begins or resumes), `SessionEndHook` (fires when a session ends), `UserPromptSubmitHook` (fires when a user submits a prompt, before LLM processing), and `PreCompactHook` (fires before context compaction). Each hook follows the existing GuardrailHook pattern with async execution.

#### Scenario: SessionStart fires on new session
- **WHEN** a new agent session is created
- **THEN** all registered SessionStartHook instances are called with session metadata

#### Scenario: SessionStart context injection
- **WHEN** a SessionStartHook returns text in its result
- **THEN** the text is injected into the LLM context for the first turn

#### Scenario: UserPromptSubmit can block
- **WHEN** a UserPromptSubmitHook returns BLOCK
- **THEN** the prompt is rejected and an error message is returned to the user

#### Scenario: SessionEnd cleanup
- **WHEN** a session ends (user disconnects, timeout, or explicit close)
- **THEN** all registered SessionEndHook instances are called for cleanup

#### Scenario: PreCompact before compression
- **WHEN** context compaction is about to occur
- **THEN** PreCompactHook instances are called, allowing backup or context preservation

### Requirement: Tool name matcher for tool hooks
The system SHALL support optional tool name matchers on `PreToolHook` and `PostToolHook`. Matchers use the syntax: exact name (`web_search`), pipe-separated (`Edit|Write`), regex (`mcp__.*`), or None/`*` for all tools. Only hooks whose matcher matches the current tool name are executed.

#### Scenario: Exact match
- **WHEN** a PreToolHook has matcher `"web_search"` and the tool name is `web_search`
- **THEN** the hook executes

#### Scenario: Exact match does not fire for different tool
- **WHEN** a PreToolHook has matcher `"web_search"` and the tool name is `bash`
- **THEN** the hook is skipped

#### Scenario: Pipe-separated match
- **WHEN** a PostToolHook has matcher `"Edit|Write"` and the tool name is `Edit`
- **THEN** the hook executes

#### Scenario: Regex match
- **WHEN** a PreToolHook has matcher `"mcp__github__.*"` and the tool name is `mcp__github__create_issue`
- **THEN** the hook executes (regex match)

#### Scenario: No matcher matches all
- **WHEN** a PreToolHook has no matcher (None) and any tool is called
- **THEN** the hook executes (backward compatible)

### Requirement: Shell command hooks
The system SHALL provide a `ShellCommandHook` implementation that executes shell commands. The hook receives event data as JSON on stdin. Exit code 0 means proceed, exit code 2 means block (stderr fed back). For SessionStart and UserPromptSubmit events, stdout is injected into LLM context. Shell execution is gated by `HOOK_SHELL_ENABLED` setting (default False).

#### Scenario: Shell hook proceeds (exit 0)
- **WHEN** a ShellCommandHook runs and the command exits with code 0
- **THEN** the hook returns ALLOW and execution continues

#### Scenario: Shell hook blocks (exit 2)
- **WHEN** a ShellCommandHook runs and the command exits with code 2
- **THEN** the hook returns BLOCK with stderr as reason

#### Scenario: Shell hook timeout
- **WHEN** a shell command exceeds the configured timeout (default 30s)
- **THEN** the process is killed and the hook returns ALLOW with a warning log

#### Scenario: Shell disabled
- **WHEN** `HOOK_SHELL_ENABLED` is False
- **THEN** no ShellCommandHook instances are created or executed

### Requirement: Hook configuration via JSON
The system SHALL support declaring hooks via JSON configuration loaded from DB-backed `HookConfigModel`. Each hook config specifies: event name, optional matcher, shell command, and timeout. Configurations are scoped to workspace or agent level.

#### Scenario: Create hook config
- **WHEN** a client creates a hook config via API with event, matcher, and command
- **THEN** the hook is stored and activated on the next matching event

#### Scenario: Workspace-level hook
- **WHEN** a hook config has `agent_id=None`
- **THEN** the hook applies to all agents in the workspace

#### Scenario: Agent-level hook
- **WHEN** a hook config has a specific `agent_id`
- **THEN** the hook applies only to that agent

### Requirement: REST API for hook management
The system SHALL expose REST API endpoints for hook configuration CRUD.

#### Scenario: List hooks
- **WHEN** a client requests `GET /api/hooks`
- **THEN** the system returns all hook configurations, optionally filtered by agent_id or event

#### Scenario: Create hook
- **WHEN** a client requests `POST /api/hooks` with hook config data
- **THEN** the system creates the hook config and returns 201

#### Scenario: Delete hook
- **WHEN** a client requests `DELETE /api/hooks/{id}`
- **THEN** the system removes the hook config and returns 204
