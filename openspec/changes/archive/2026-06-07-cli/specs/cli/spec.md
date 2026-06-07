## ADDED Requirements

### Requirement: CLI entry point and command structure
The system SHALL provide a `hecate` CLI command registered as a `console_scripts` entry point in pyproject.toml, using `typer` framework with nested subcommand groups mapping to API resource domains.

#### Scenario: CLI help shows all resource groups
- **WHEN** `hecate --help` is executed
- **THEN** the output SHALL list subcommand groups: agent, session, chat, kb, tool, skill, workflow, prompt, memory, template, conversation, model, config, auth

#### Scenario: Resource group help shows actions
- **WHEN** `hecate agent --help` is executed
- **THEN** the output SHALL list actions: list, create, get, update, delete

#### Scenario: Unknown command returns error
- **WHEN** `hecate nonexistent --help` is executed
- **THEN** the CLI SHALL exit with non-zero code and display error message

### Requirement: Configuration management via TOML profiles
The system SHALL read and write CLI configuration from `~/.hecate/config.toml`, supporting named profiles with `base_url`, `api_key`, and `output` settings. The `default` profile SHALL be used when no `--profile` flag is provided.

#### Scenario: Default configuration
- **WHEN** `~/.hecate/config.toml` does not exist and no env vars are set
- **THEN** the CLI SHALL use `base_url=http://localhost:8000` and prompt for `api_key` on first authenticated command

#### Scenario: Set configuration value
- **WHEN** `hecate config set api_key hec-xxxxx` is executed
- **THEN** the CLI SHALL write the value to the active profile in `~/.hecate/config.toml`

#### Scenario: Use named profile
- **WHEN** `hecate --profile staging agent list` is executed
- **THEN** the CLI SHALL read the `staging` profile's `base_url` and `api_key` from config

#### Scenario: Show current configuration
- **WHEN** `hecate config show` is executed
- **THEN** the CLI SHALL display the active profile's settings with `api_key` masked

### Requirement: Dual authentication — API Key and JWT
The CLI SHALL support authentication via direct API key storage and JWT login with automatic token refresh.

#### Scenario: Authenticate with stored API key
- **WHEN** a command is executed and the active profile has `api_key` set
- **THEN** the CLI SHALL send `Authorization: Bearer <api_key>` header with all API requests

#### Scenario: Login with JWT
- **WHEN** `hecate auth login --email user@example.com` is executed with correct credentials
- **THEN** the CLI SHALL call `POST /api/auth/login`, store the access token and refresh token in the profile, and use the access token for subsequent requests

#### Scenario: JWT auto-refresh on expiry
- **WHEN** an API request returns 401 and the profile has a refresh token
- **THEN** the CLI SHALL call `POST /api/auth/refresh` with the refresh token, update the stored access token, and retry the original request

#### Scenario: Whoami shows current user
- **WHEN** `hecate auth whoami` is executed
- **THEN** the CLI SHALL call `GET /api/auth/me` and display the current user's email and ID

### Requirement: Output formatting — table and JSON
The CLI SHALL display output in rich tables by default and support a `--json` flag for machine-readable JSON output.

#### Scenario: Default table output
- **WHEN** `hecate agent list` is executed without `--json`
- **THEN** the CLI SHALL render a rich table with columns for id, name, mode, and model_config

#### Scenario: JSON output
- **WHEN** `hecate agent list --json` is executed
- **THEN** the CLI SHALL print the raw JSON response to stdout for piping to jq or other tools

### Requirement: HTTP client wrapper
The CLI SHALL use `httpx` synchronous client for all API communication, with configurable timeout and automatic error handling.

#### Scenario: Successful API call
- **WHEN** a CLI command makes a GET request to `/api/agents`
- **THEN** the client SHALL return the parsed JSON response

#### Scenario: API error with user-friendly message
- **WHEN** the API returns a 4xx or 5xx error
- **THEN** the CLI SHALL display the error code and message from the API response and exit with non-zero code

#### Scenario: Connection refused
- **WHEN** the API server is not reachable
- **THEN** the CLI SHALL display "Error: Cannot connect to Hecate server at <base_url>" and exit with code 1

### Requirement: Agent CRUD commands
The CLI SHALL provide `hecate agent` subcommands for agent lifecycle management.

#### Scenario: List agents
- **WHEN** `hecate agent list` is executed
- **THEN** the CLI SHALL call `GET /api/agents` and display agents in a table

#### Scenario: Create agent
- **WHEN** `hecate agent create --name "Test" --model gpt-4o --mode chat` is executed
- **THEN** the CLI SHALL call `POST /api/agents` with the provided parameters and display the created agent

#### Scenario: Get agent
- **WHEN** `hecate agent get <agent_id>` is executed
- **THEN** the CLI SHALL call `GET /api/agents/{id}` and display the agent details

#### Scenario: Update agent
- **WHEN** `hecate agent update <agent_id> --name "New Name"` is executed
- **THEN** the CLI SHALL call `PUT /api/agents/{id}` with the updated fields

#### Scenario: Delete agent
- **WHEN** `hecate agent delete <agent_id>` is executed
- **THEN** the CLI SHALL prompt for confirmation and call `DELETE /api/agents/{id}`

### Requirement: Session management commands
The CLI SHALL provide `hecate session` subcommands for session lifecycle.

#### Scenario: Create session
- **WHEN** `hecate session create --agent-id <id>` is executed
- **THEN** the CLI SHALL call `POST /api/sessions` and return the session ID

#### Scenario: List sessions
- **WHEN** `hecate session list` is executed
- **THEN** the CLI SHALL call `GET /api/sessions` and display sessions in a table

#### Scenario: Resume interrupted session
- **WHEN** `hecate session resume <session_id> --message "approved"` is executed
- **THEN** the CLI SHALL call `POST /api/sessions/{id}/resume` with the resume value

### Requirement: Chat commands with streaming
The CLI SHALL provide `hecate chat send` for one-shot messages and `hecate chat interactive` for interactive REPL sessions with SSE streaming.

#### Scenario: One-shot chat message
- **WHEN** `hecate chat send <agent_id> "Hello"` is executed
- **THEN** the CLI SHALL call `POST /v1/chat/completions` with the message and display the assistant's response

#### Scenario: Interactive chat with streaming
- **WHEN** `hecate chat interactive <agent_id>` is executed
- **THEN** the CLI SHALL open an interactive REPL that sends messages to the agent with `stream=true`, displaying response tokens incrementally as they arrive

#### Scenario: Interactive chat slash commands
- **WHEN** the user types `/clear`, `/exit`, or `/history` in interactive mode
- **THEN** the CLI SHALL handle the slash command accordingly (clear context, exit, show conversation history)

#### Scenario: Streaming SSE parsing
- **WHEN** the API returns SSE events during streaming
- **THEN** the CLI SHALL parse `data: {...}` lines, extract `choices[0].delta.content`, and print each token without newline

### Requirement: Knowledge base commands
The CLI SHALL provide `hecate kb` subcommands for knowledge base and document management.

#### Scenario: List knowledge bases
- **WHEN** `hecate kb list` is executed
- **THEN** the CLI SHALL call `GET /api/knowledge-bases` and display KBs in a table

#### Scenario: Create knowledge base
- **WHEN** `hecate kb create --name "My KB" --description "Test"` is executed
- **THEN** the CLI SHALL call `POST /api/knowledge-bases` and return the created KB

#### Scenario: Upload document to knowledge base
- **WHEN** `hecate kb upload <kb_id> document.pdf` is executed
- **THEN** the CLI SHALL call `POST /api/knowledge-bases/{id}/documents` with multipart file upload

#### Scenario: List documents in knowledge base
- **WHEN** `hecate kb documents <kb_id>` is executed
- **THEN** the CLI SHALL call `GET /api/knowledge-bases/{id}/documents` and display documents with parsing status

### Requirement: Tool commands
The CLI SHALL provide `hecate tool` subcommands for tool listing.

#### Scenario: List tools
- **WHEN** `hecate tool list` is executed
- **THEN** the CLI SHALL call `GET /api/tools` and display tools in a table

#### Scenario: List tools filtered by source
- **WHEN** `hecate tool list --source builtin` is executed
- **THEN** the CLI SHALL call `GET /api/tools?source=builtin` and display only builtin tools

### Requirement: Skill CRUD commands
The CLI SHALL provide `hecate skill` subcommands for skill management.

#### Scenario: List skills
- **WHEN** `hecate skill list` is executed
- **THEN** the CLI SHALL call `GET /api/skills` and display skills in a table

#### Scenario: Import skill from SKILL.md
- **WHEN** `hecate skill import skill.md` is executed
- **THEN** the CLI SHALL call `POST /api/skills/import` with the file upload

### Requirement: Workflow commands
The CLI SHALL provide `hecate workflow` subcommands for workflow CRUD, versioning, validation, and test runs.

#### Scenario: List workflows
- **WHEN** `hecate workflow list` is executed
- **THEN** the CLI SHALL call `GET /api/workflows` and display workflows in a table

#### Scenario: Validate workflow
- **WHEN** `hecate workflow validate <workflow_id>` is executed
- **THEN** the CLI SHALL call `POST /api/workflows/{id}/validate` and display validation results

#### Scenario: Test run workflow
- **WHEN** `hecate workflow test-run <workflow_id>` is executed
- **THEN** the CLI SHALL call `POST /api/workflows/{id}/test-run` and display the execution result

### Requirement: Prompt commands
The CLI SHALL provide `hecate prompt` subcommands for prompt CRUD and version management.

#### Scenario: List prompts
- **WHEN** `hecate prompt list` is executed
- **THEN** the CLI SHALL call `GET /api/prompts` and display prompts in a table

#### Scenario: Get prompt by label
- **WHEN** `hecate prompt by-label production` is executed
- **THEN** the CLI SHALL call `GET /api/prompts/by-label/production` and display the prompt content

### Requirement: Memory commands
The CLI SHALL provide `hecate memory` subcommands for memory blocks and user memories.

#### Scenario: List agent memory blocks
- **WHEN** `hecate memory blocks <agent_id>` is executed
- **THEN** the CLI SHALL call `GET /api/agents/{id}/memory-blocks` and display blocks in a table

#### Scenario: Search user memories
- **WHEN** `hecate memory search <query>` is executed
- **THEN** the CLI SHALL call `GET /api/memory?q=<query>` and display matching memories

### Requirement: Template commands
The CLI SHALL provide `hecate template` subcommands for agent and orchestration templates.

#### Scenario: List agent templates
- **WHEN** `hecate template agents` is executed
- **THEN** the CLI SHALL call `GET /api/agent-templates` and display available templates

#### Scenario: Instantiate agent template
- **WHEN** `hecate template agents instantiate <template_id> --name "My Agent"` is executed
- **THEN** the CLI SHALL call `POST /api/agent-templates/{id}/instantiate` and return the created agent

### Requirement: Conversation commands
The CLI SHALL provide `hecate conversation` subcommands for conversation management.

#### Scenario: List conversations
- **WHEN** `hecate conversation list` is executed
- **THEN** the CLI SHALL call `GET /api/conversations` and display conversations in a table

#### Scenario: Get conversation with messages
- **WHEN** `hecate conversation get <conversation_id>` is executed
- **THEN** the CLI SHALL call `GET /api/conversations/{id}` and display the conversation with all messages

### Requirement: Model provider commands
The CLI SHALL provide `hecate model` subcommands for model listing and provider management.

#### Scenario: List available models
- **WHEN** `hecate model list` is executed
- **THEN** the CLI SHALL call `GET /v1/models` and display models in a table

#### Scenario: Test model provider connectivity
- **WHEN** `hecate model providers test <provider_id>` is executed
- **THEN** the CLI SHALL call `POST /api/model-providers/{id}/test` and display the test result

### Requirement: Pagination support
The CLI SHALL support `--page` and `--page-size` flags for all list commands, defaulting to page=1 and page_size=20.

#### Scenario: Custom pagination
- **WHEN** `hecate agent list --page 2 --page-size 10` is executed
- **THEN** the CLI SHALL call `GET /api/agents?page=2&page_size=10` and display the results

### Requirement: CLI dependencies
The CLI SHALL add `typer>=0.15.0` and `rich>=13.0.0` to the main dependencies in pyproject.toml, and register a `hecate` console_scripts entry point pointing to `hecate.cli.main:app`.

#### Scenario: Install and run CLI
- **WHEN** `uv pip install -e .` is executed
- **THEN** the `hecate` command SHALL be available in the shell
