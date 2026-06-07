## 1. Setup — Dependencies and Entry Point

- [x] 1.1 Add `typer>=0.15.0` and `rich>=13.0.0` to main dependencies in pyproject.toml
- [x] 1.2 Add `[project.scripts]` entry point: `hecate = "hecate.cli.main:app"`
- [x] 1.3 Install new dependencies: `uv pip install -e ".[dev]"`
- [x] 1.4 Create `src/hecate/cli/__init__.py` (empty)
- [x] 1.5 Create `src/hecate/cli/commands/__init__.py` (empty)

## 2. Core Infrastructure

- [x] 2.1 Implement `src/hecate/cli/config.py` — TOML config loader, profile management, `get_config()`, `set_config()`, `get_active_profile()`, config file creation on first run
- [x] 2.2 Implement `src/hecate/cli/client.py` — HecateClient class with httpx sync client, auth header injection, error handling, connection check
- [x] 2.3 Implement `src/hecate/cli/output.py` — `format_table()` using rich Table, `format_json()` for --json output, `display_error()` for API errors, `confirm_delete()` for destructive actions
- [x] 2.4 Implement `src/hecate/cli/main.py` — Root typer app, `--profile` option, `--json` global flag, `--version` flag, register all subcommand groups

## 3. Config and Auth Commands

- [x] 3.1 Implement `hecate config set <key> <value>` — write to active profile in config.toml
- [x] 3.2 Implement `hecate config get <key>` — display single config value
- [x] 3.3 Implement `hecate config show` — display all config values with api_key masked
- [x] 3.4 Implement `hecate auth login --email <email>` — call POST /api/auth/login, store tokens in profile
- [x] 3.5 Implement `hecate auth whoami` — call GET /api/auth/me, display user info
- [x] 3.6 Implement JWT auto-refresh in client.py — check token expiry, call POST /api/auth/refresh when needed

## 4. Agent Commands

- [x] 4.1 Implement `hecate agent list` — GET /api/agents, table output with id/name/mode/model
- [x] 4.2 Implement `hecate agent create` — POST /api/agents with --name, --model, --mode, --persona, --tools, --kb-ids
- [x] 4.3 Implement `hecate agent get <id>` — GET /api/agents/{id}, detailed display
- [x] 4.4 Implement `hecate agent update <id>` — PUT /api/agents/{id} with optional --name, --persona, --tools, --kb-ids
- [x] 4.5 Implement `hecate agent delete <id>` — DELETE /api/agents/{id} with confirmation prompt

## 5. Session Commands

- [x] 5.1 Implement `hecate session create --agent-id <id>` — POST /api/sessions
- [x] 5.2 Implement `hecate session list` — GET /api/sessions, table output
- [x] 5.3 Implement `hecate session get <id>` — GET /api/sessions/{id}
- [x] 5.4 Implement `hecate session resume <id> --message <msg>` — POST /api/sessions/{id}/resume

## 6. Chat Commands (Core Experience)

- [x] 6.1 Implement `hecate chat send <agent_id> <message>` — POST /v1/chat/completions (non-streaming), display response
- [x] 6.2 Implement SSE streaming parser in client.py — parse `data: {...}` lines, extract delta content
- [x] 6.3 Implement `hecate chat interactive <agent_id>` — REPL loop with streaming, slash commands (/clear, /exit, /history)
- [x] 6.4 Implement interactive chat context management — maintain conversation_id across turns, support --session-id for resuming

## 7. Knowledge Base Commands

- [x] 7.1 Implement `hecate kb list` — GET /api/knowledge-bases, table output
- [x] 7.2 Implement `hecate kb create` — POST /api/knowledge-bases with --name, --description, --embedding-model, --chunk-strategy
- [x] 7.3 Implement `hecate kb upload <kb_id> <file>` — POST /api/knowledge-bases/{id}/documents with multipart upload
- [x] 7.4 Implement `hecate kb documents <kb_id>` — GET /api/knowledge-bases/{id}/documents, table with parsing status

## 8. Tool Commands

- [x] 8.1 Implement `hecate tool list` — GET /api/tools with optional --source filter
- [x] 8.2 Implement `hecate tool get <id>` — GET /api/tools/{id}

## 9. Skill Commands

- [x] 9.1 Implement `hecate skill list` — GET /api/skills, table output
- [x] 9.2 Implement `hecate skill create` — POST /api/skills with --name, --content, --source
- [x] 9.3 Implement `hecate skill get <id>` — GET /api/skills/{id}
- [x] 9.4 Implement `hecate skill update <id>` — PUT /api/skills/{id}
- [x] 9.5 Implement `hecate skill delete <id>` — DELETE /api/skills/{id} with confirmation
- [x] 9.6 Implement `hecate skill import <file>` — POST /api/skills/import with file upload

## 10. Workflow Commands

- [x] 10.1 Implement `hecate workflow list` — GET /api/workflows, table output
- [x] 10.2 Implement `hecate workflow create` — POST /api/workflows with --name, --graph-dsl (JSON string or file path)
- [x] 10.3 Implement `hecate workflow get <id>` — GET /api/workflows/{id}
- [x] 10.4 Implement `hecate workflow update <id>` — PUT /api/workflows/{id}
- [x] 10.5 Implement `hecate workflow delete <id>` — DELETE /api/workflows/{id} with confirmation
- [x] 10.6 Implement `hecate workflow validate <id>` — POST /api/workflows/{id}/validate
- [x] 10.7 Implement `hecate workflow test-run <id>` — POST /api/workflows/{id}/test-run
- [x] 10.8 Implement `hecate workflow versions <id>` — GET /api/workflows/{id}/versions
- [x] 10.9 Implement `hecate workflow runs <id>` — GET /api/workflows/{id}/runs

## 11. Prompt Commands

- [x] 11.1 Implement `hecate prompt list` — GET /api/prompts, table output
- [x] 11.2 Implement `hecate prompt create` — POST /api/prompts with --name, --content, --label
- [x] 11.3 Implement `hecate prompt get <id>` — GET /api/prompts/{id}
- [x] 11.4 Implement `hecate prompt update <id>` — PUT /api/prompts/{id}
- [x] 11.5 Implement `hecate prompt delete <id>` — DELETE /api/prompts/{id} with confirmation
- [x] 11.6 Implement `hecate prompt versions <id>` — GET /api/prompts/{id}/versions
- [x] 11.7 Implement `hecate prompt by-label <label>` — GET /api/prompts/by-label/{label}

## 12. Memory Commands

- [x] 12.1 Implement `hecate memory blocks <agent_id>` — GET /api/agents/{id}/memory-blocks
- [x] 12.2 Implement `hecate memory blocks create <agent_id>` — POST /api/agents/{id}/memory-blocks with --label, --content
- [x] 12.3 Implement `hecate memory blocks update <agent_id> <block_id>` — PUT /api/agents/{id}/memory-blocks/{block_id}
- [x] 12.4 Implement `hecate memory blocks delete <agent_id> <block_id>` — DELETE with confirmation
- [x] 12.5 Implement `hecate memory list` — GET /api/memory, user memories table
- [x] 12.6 Implement `hecate memory search <query>` — GET /api/memory?q=<query>

## 13. Template Commands

- [x] 13.1 Implement `hecate template agents` — GET /api/agent-templates
- [x] 13.2 Implement `hecate template agents instantiate <id>` — POST /api/agent-templates/{id}/instantiate
- [x] 13.3 Implement `hecate template orchestration` — GET /api/orchestration-templates

## 14. Conversation Commands

- [x] 14.1 Implement `hecate conversation list` — GET /api/conversations, table output
- [x] 14.2 Implement `hecate conversation get <id>` — GET /api/conversations/{id}, display messages

## 15. Model Commands

- [x] 15.1 Implement `hecate model list` — GET /v1/models, table output
- [x] 15.2 Implement `hecate model providers list` — GET /api/model-providers
- [x] 15.3 Implement `hecate model providers create` — POST /api/model-providers
- [x] 15.4 Implement `hecate model providers test <id>` — POST /api/model-providers/{id}/test

## 16. Message Commands

- [x] 16.1 Implement `hecate message citations <message_id>` — GET /api/messages/{id}/citations

## 17. Verification

- [x] 17.1 Run `ruff check src/hecate/ tests/` — zero errors
- [x] 17.2 Run `ruff format --check src/ tests/` — zero errors
- [x] 17.3 Run `mypy src/` — zero errors
- [x] 17.4 Run `python -m pytest tests/ -q` — all tests pass
