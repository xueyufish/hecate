# CLI Reference

Hecate ships three console-script entry points, all installed via `uv pip install -e ".[dev]"`.

For the conceptual model of the CLI (why three entry points, how it relates to the API), see [Command-Line Interface concept](../concepts/cli.md). For the API reference, see [REST API](rest-api.md).

---

## Entry points

| Entry point | Purpose | Backend required? |
|---|---|---|
| `hecate` | Day-to-day operations | Yes |
| `hecate-migrate` | Database migrations only | No (DB only) |
| `hecate-flag-audit` | Feature flag audits | No (read-only) |

See [CLI concept](../concepts/cli.md#the-three-entry-points) for why they're separate.

---

## Global options

Every `hecate` command supports:

| Flag | Description | Default |
|---|---|---|
| `--profile <name>` | Named profile from `~/.hecate/config.toml` | `default` |
| `--json` | Output JSON instead of table | `false` (table) |
| `--api-url <url>` | Override Hecate API URL | From profile |
| `--api-key <key>` | Override API key | From profile / env |
| `--verbose` | Verbose logging | `false` |
| `--no-color` | Disable ANSI colors | `false` |

---

## `hecate agent` — Agent CRUD

```bash
hecate agent --help
```

### `hecate agent list`

```bash
hecate agent list [--workspace <id>] [--mode <mode>] [--name <pattern>] [--limit <n>] [--format <table|json>]
```

| Flag | Description |
|---|---|
| `--workspace <id>` | Filter by workspace |
| `--mode <mode>` | Filter by execution mode: `chat`, `three_layer`, `workflow` |
| `--name <pattern>` | Filter by name (regex) |
| `--limit <n>` | Max results (default 50) |
| `--format <table\|json>` | Output format (overrides `--json`) |

Examples:

```bash
# List all agents in current workspace
hecate agent list

# JSON output for piping to jq
hecate agent list --json | jq '.[] | select(.name | contains("test"))'

# Filter by mode
hecate agent list --mode workflow
```

### `hecate agent create`

```bash
hecate agent create \
  --name <name> \
  --model <model> \
  --mode <mode> \
  [--persona <text>] \
  [--tools <csv>] \
  [--skills <csv>] \
  [--knowledge-bases <csv>] \
  [--risk-level <low|medium|high>] \
  [--temperature <0.0-2.0>]
```

| Flag | Required | Description |
|---|---|---|
| `--name` | Yes | Unique agent name (1-255 chars) |
| `--model` | Yes | Model name (e.g., `gpt-4o-mini`) |
| `--mode` | Yes | `chat`, `three_layer`, or `workflow` |
| `--persona` | No | System prompt |
| `--tools` | No | Comma-separated tool names |
| `--skills` | No | Comma-separated skill names |
| `--knowledge-bases` | No | Comma-separated KB IDs |
| `--risk-level` | No | `low` (default), `medium`, `high` |
| `--temperature` | No | LLM temperature (default 0.7) |

Example:

```bash
hecate agent create \
  --name "tech-support" \
  --model "gpt-4o-mini" \
  --mode chat \
  --persona "You are a patient technical support engineer." \
  --tools "web_search,read_file" \
  --risk-level low
```

### `hecate agent get`

```bash
hecate agent get <agent-id> [--format <table|json>]
```

### `hecate agent update`

```bash
hecate agent update <agent-id> \
  [--name <name>] [--model <model>] [--mode <mode>] [--persona <text>] \
  [--tools <csv>] [--skills <csv>] [--temperature <0.0-2.0>]
```

PATCH semantics — only specified fields are updated.

### `hecate agent delete`

```bash
hecate agent delete <agent-id> [--force]
```

Soft delete by default. `--force` skips confirmation.

### `hecate agent export`

```bash
hecate agent export <agent-id> [--output <file>]
```

Exports agent JSON to stdout (or file). Use for backup or migration.

### `hecate agent import`

```bash
hecate agent import --file <file> [--workspace <id>]
```

Imports agent JSON. IDs may be regenerated.

---

## `hecate workflow` — Workflow CRUD

```bash
hecate workflow --help
```

### `hecate workflow list`

```bash
hecate workflow list [--workspace <id>] [--tag <tag>] [--limit <n>] [--format <table|json>]
```

### `hecate workflow create`

```bash
hecate workflow create \
  --name <name> \
  --file <dsl.json> \
  [--workspace <id>] \
  [--description <text>]
```

The DSL file is the JSON graph format (see [Graph DSL](graph-dsl.md)).

### `hecate workflow get`

```bash
hecate workflow get <workflow-id> [--version <v>]
```

### `hecate workflow update`

```bash
hecate workflow update <workflow-id> --file <new-dsl.json>
```

Creates a new version. Old versions remain accessible.

### `hecate workflow delete`

```bash
hecate workflow delete <workflow-id> [--force]
```

### `hecate workflow validate`

```bash
hecate workflow validate --file <dsl.json>
```

Validates the DSL without creating. Returns exit code 0 on success, 1 on errors.

### `hecate workflow run`

```bash
hecate workflow run <workflow-id> \
  --input <input.json> \
  [--session-id <id>]
```

Returns the execution ID; the workflow runs asynchronously.

### `hecate workflow versions`

```bash
hecate workflow versions <workflow-id>
```

Lists all versions of a workflow.

### `hecate workflow runs`

```bash
hecate workflow runs <workflow-id> [--limit <n>]
```

Lists execution history.

---

## `hecate chat` — Chat with agents

### `hecate chat send`

```bash
hecate chat send <agent-id> "<message>" \
  [--session-id <id>] \
  [--stream] \
  [--output <text|json>]
```

Send one message, print response. With `--stream`, tokens appear as they arrive.

### `hecate chat interactive`

```bash
hecate chat interactive <agent-id> [--session-id <id>]
```

REPL with slash commands:

| Command | Effect |
|---|---|
| `/clear` | Reset context |
| `/history` | Show messages so far |
| `/exit` or `/quit` | Exit REPL |
| `/help` | Show commands |

---

## `hecate session` — Session management

```bash
hecate session list [--agent <id>] [--status <active|interrupted|completed|error>] [--limit <n>]
hecate session get <session-id>
hecate session create --agent <agent-id> [--metadata <json>]
hecate session resume <session-id> --message <text>
```

Use `resume` to continue an interrupted session after human-in-the-loop approval.

---

## `hecate kb` — Knowledge base

```bash
hecate kb list
hecate kb create --name <name> --source <path-or-url>
hecate kb upload <kb-id> --file <path>
hecate kb documents <kb-id> [--limit <n>]
```

For the full RAG pipeline, see [Knowledge and Retrieval concept](../concepts/knowledge-rag.md).

---

## `hecate tool` — Tool management

```bash
hecate tool list [--source <builtin|custom|mcp>]
hecate tool get <tool-name>
```

`--source` filters by tool origin. For plugin tools, see [Plugins concept](../concepts/plugins.md).

---

## `hecate skill` — Skill management

```bash
hecate skill list [--tag <tag>]
hecate skill get <skill-name>[@<version>]
hecate skill create --name <name> --prompt-file <path> --tools <csv>
hecate skill update <skill-name> --prompt-file <path>
hecate skill enable <skill-name>
hecate skill disable <skill-name>
hecate skill delete <skill-name>
```

For skill lifecycle, see [Skills concept](../concepts/skills.md).

---

## `hecate prompt` — Prompt versioning

```bash
hecate prompt list
hecate prompt get <prompt-id>
hecate prompt create --name <name> --content <text>
hecate prompt update <prompt-id> --content <text>
hecate prompt versions <prompt-id>
```

Auto-versions on each update. Use `--label <label>` to tag versions.

---

## `hecate memory` — Memory management

```bash
hecate memory list [--agent <id>] [--user <user-id>] [--level <working|user|knowledge>]
hecate memory search <query> [--agent <id>] [--limit <n>]
hecate memory delete <memory-id>
```

For 4-level memory architecture, see [Memory System concept](../concepts/memory.md).

---

## `hecate model` — Model and provider

```bash
hecate model list [--provider <name>]
hecate model list-providers
hecate model test <provider-model> --prompt "ping"
```

Use `test` to verify a provider's API key and connectivity.

---

## `hecate conversation` — Conversation browsing

```bash
hecate conversation list [--session <id>] [--limit <n>]
hecate conversation get <conversation-id>
```

Read-only. For conversation analytics, use the Canvas Ops Center.

---

## `hecate template` — Templates

```bash
hecate template list
hecate template instantiate <template-id> --name <name> [--var <key=value>]
hecate template orchestration <orchestration-id>
```

Templates are pre-built agent / workflow configurations.

---

## `hecate config` — CLI configuration

```bash
hecate config set api-url <url>
hecate config set api-key <key>
hecate config set output <table|json>
hecate config show
hecate config profiles
```

Stored in `~/.hecate/config.toml` (Linux) or `~/Library/Application Support/hecate/config.toml` (macOS).

---

## `hecate auth` — Authentication

```bash
hecate auth login [--profile <name>]
hecate auth whoami
hecate auth logout [--profile <name>]
```

`login` triggers the OIDC / SAML / LDAP flow if SSO is configured. Otherwise it stores the API key from the env var.

---

## `hecate preflight` — Health check

```bash
hecate preflight [--json]
```

Verifies the deployment is correctly set up before bringing Hecate online. Checks:

- Database connection
- Migrations current
- LLM provider API keys valid
- Vector store reachable
- Redis (if configured) reachable
- Backup storage reachable

Returns exit code 0 on success, 1 on any failure.

---

## `hecate-migrate` — Standalone migration runner

```bash
hecate-migrate upgrade head      # Apply all pending
hecate-migrate downgrade -1      # Roll back one
hecate-migrate current          # Show current revision
hecate-migrate history          # Show history
hecate-migrate plan <a> <b>      # Show operations from a to b
hecate-migrate stamp head        # Mark current without running
```

Designed for Docker Compose init service, Kubernetes init container, or Helm pre-install hook. Exits 0 on success, non-zero on failure — the orchestrator decides whether to proceed.

**Environment**: requires `DATABASE_URL` only. No full Hecate config needed.

---

## `hecate-flag-audit` — Feature flag audits

```bash
hecate-flag-audit list                  # All flags + status
hecate-flag-audit show <flag-name>      # Detail
hecate-flag-audit history <flag-name>   # Change history
hecate-flag-audit compare <env1> <env2> # Diff environments
hecate-flag-audit report                # Compliance report
```

Read-only by design. Safe to run in production at any time.

---

## Output formatting

### Table (default)

```
┏━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┓
┃ id                       ┃ mode   ┃ name                ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━┩
│ a1b2c3d4-...             │ chat   │ Tech Support Agent  │
│ b2c3d4e5-...             │ chat   │ Creative Writing…   │
└──────────────────────────┴────────┴─────────────────────┘
```

### JSON (`--json`)

```json
[
  {"id": "a1b2c3d4-...", "name": "Tech Support Agent", "mode": "chat"},
  {"id": "b2c3d4e5-...", "name": "Creative Writing…", "mode": "chat"}
]
```

### Pipe to `jq`

```bash
hecate agent list --json | jq '.[] | select(.mode == "workflow") | .id'
```

---

## Common patterns

### Bulk operations

```bash
# Delete all agents matching a pattern
hecate agent list --json | jq -r '.[] | select(.name | contains("test-")) | .id' | \
  xargs -I {} hecate agent delete {} --force

# Re-run all workflows for a tag
hecate workflow list --tag nightly --json | jq -r '.[].id' | \
  xargs -I {} hecate workflow run {} --input '{}'
```

### CI/CD

```yaml
# .github/workflows/deploy.yml
- name: Run migrations
  run: hecate-migrate upgrade head
  env:
    DATABASE_URL: ${{ secrets.DATABASE_URL }}

- name: Health check
  run: |
    hecate preflight
    hecate agent get ${{ steps.verify.outputs.test_agent_id }}
```

### Scripting

```bash
#!/bin/bash
set -euo pipefail

# Wait for Hecate to be ready
until hecate preflight; do
  echo "Waiting for Hecate..."
  sleep 5
done

# Create an agent
AGENT_ID=$(hecate agent create \
  --name "ci-test-agent" \
  --model "gpt-4o-mini" \
  --mode chat \
  --json | jq -r '.id')

# Clean up
hecate agent delete "$AGENT_ID" --force
```

---

## Environment variables

The CLI respects these env vars (overriding profile config):

| Variable | Default | Description |
|---|---|---|
| `HECATE_API_URL` | `http://localhost:8000` | API base URL |
| `HECATE_API_KEY` | (none) | API key for auth |
| `HECATE_PROFILE` | `default` | Profile name |
| `HECATE_OUTPUT` | `table` | Default output format |
| `HECATE_NO_COLOR` | `false` | Disable ANSI colors |
| `HECATE_CONFIG_DIR` | `~/.hecate` | Config directory |

---

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | General error |
| 2 | Invalid arguments |
| 3 | Authentication failed |
| 4 | Resource not found |
| 5 | Permission denied |
| 6 | Conflict (e.g., duplicate name) |
| 7 | Validation failed |
| 8 | Backend unavailable |
| 9 | Rate limit exceeded |
| 10 | Budget exceeded |

---

## Related documents

- [CLI concept](../concepts/cli.md) — why three entry points, design rationale
- [Cookbook](../how-to/cookbook.md) — copy-paste recipes
- [REST API reference](rest-api.md) — the HTTP API the CLI wraps
- [Quickstart](../getting-started/quickstart.md) — first-time setup
- [Tutorial: Build Your First Agent](../tutorials/01-first-agent.md) — uses these commands extensively