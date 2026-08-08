# CLI Reference

Hecate ships two console-script entry points, both installed via `uv pip install -e ".[dev]"`.

## `hecate` — main CLI

A [Typer](https://typer.tiangolo.com/)-based CLI with nested subcommand groups for managing agents, sessions, knowledge bases, workflows, and other resources.

```bash
hecate --help
```

### Command groups

| Group | Purpose |
|-------|---------|
| `hecate agent` | Create, list, get, update, delete agents |
| `hecate session` | Create, list, resume sessions |
| `hecate chat` | One-shot or interactive chat with streaming |
| `hecate kb` | Knowledge base and document management |
| `hecate tool` | List and manage tools |
| `hecate skill` | Skill CRUD and import |
| `hecate workflow` | Workflow CRUD, validation, test runs |
| `hecate prompt` | Prompt CRUD and version management |
| `hecate memory` | Memory blocks and user memories |
| `hecate model` | Model listing and provider management |
| `hecate conversation` | Conversation management |
| `hecate template` | Agent and orchestration templates |
| `hecate config` | CLI configuration (`~/.hecate/config.toml`) |
| `hecate auth` | Login, whoami, token management |

### Output formatting

By default, output is rendered in rich tables. Use `--json` for machine-readable JSON output (for piping to `jq` or scripts).

### Profiles

The CLI reads configuration from `~/.hecate/config.toml`. Named profiles are supported via `--profile <name>`, each with its own `base_url`, `api_key`, and `output` settings.

## `hecate-migrate` — standalone migration runner

```bash
hecate-migrate          # runs alembic upgrade head
hecate-migrate --help   # see all options
```

Designed for one-shot use as a Docker Compose init service, Kubernetes init container, or Helm pre-install hook. Runs Alembic migrations without booting the full web application.