# Command-Line Interface (CLI)

Hecate ships three command-line entry points — `hecate`, `hecate-migrate`, and `hecate-flag-audit` — for managing agents, workflows, knowledge bases, and operations. The CLI is a **first-class interface**, not a thin wrapper over the API.

This document explains the **conceptual model**: what the CLI is for, how the three entry points relate, and when to use the CLI vs the API vs the visual canvas. For the full command reference, see [CLI Reference](../reference/cli.md).

---

## The three entry points

Hecate ships three separate executables, each with a distinct purpose:

| Entry point | Purpose | When to use |
|---|---|---|
| `hecate` | Day-to-day operations: agents, workflows, knowledge bases, sessions | "I want to do X with Hecate" |
| `hecate-migrate` | Database schema migrations only | "I'm upgrading Hecate" |
| `hecate-flag-audit` | Feature flag audits (compliance review) | "I need to prove which flags are active in production" |

These are **separate executables**, not subcommands of one. They have independent lifecycles — you can use `hecate-migrate` without running Hecate, and vice versa.

---

## Why three entry points?

A single monolithic CLI seems simpler but creates problems:

- `hecate-migrate` runs **before** Hecate is available (during deploys, in CI, in init containers)
- `hecate-flag-audit` reads **production state** without mutating it — different security posture
- Mixing concerns in one binary means one bug can take down unrelated operations

By separating them, each tool:

- Has its own dependency surface (smaller binary)
- Has its own permissions model
- Can be deployed / sandboxed independently
- Has independent test coverage

---

## `hecate` — the main CLI

The primary entry point. Subcommands group by domain:

| Subcommand | What it manages |
|---|---|
| `hecate agent` | Agents: create, list, get, update, delete, export, import |
| `hecate workflow` | Workflows: create, list, get, update, delete, validate, run |
| `hecate kb` | Knowledge bases: create, upload, documents |
| `hecate skill` | Skills: create, list, get, update, delete |
| `hecate tool` | Tools: list, get |
| `hecate chat` | Chat with agents: send, interactive REPL |
| `hecate session` | Sessions: create, list, get, resume |
| `hecate conversation` | Conversations: list, get |
| `hecate message` | Messages: citations |
| `hecate memory` | Memory: list, create, update, delete, search |
| `hecate prompt` | Prompts: create, list, get, update, delete, versions |
| `hecate template` | Templates: list, instantiate, orchestration |
| `hecate model` | Models: list, providers, test |
| `hecate config` | CLI configuration: set, get, profiles |
| `hecate auth` | Authentication: login, whoami |

Each subcommand has consistent structure: `hecate <subcommand> <action> [flags] [args]`.

---

## `hecate-migrate` — schema migrations

A **separate executable** that runs Alembic migrations. Independent of the Hecate app.

```bash
hecate-migrate upgrade head      # Apply all pending migrations
hecate-migrate downgrade -1      # Roll back one migration
hecate-migrate current          # Show current revision
hecate-migrate history          # Show migration history
```

**Why separate from `hecate`**:

- Runs **before** Hecate is installed / available
- Used in Docker Compose init containers, Kubernetes init containers, Helm pre-install hooks
- Different security model: read/write on `DATABASE_URL` but no need for full Hecate runtime

Set via `[project.scripts]` in `pyproject.toml`:

```toml
[project.scripts]
hecate = "hecate.cli.main:app"
hecate-migrate = "hecate.cli.migrate:main"
hecate-flag-audit = "hecate.cli.flag_audit:main"
```

---

## `hecate-flag-audit` — feature flag audits

Compliance and engineering teams need to answer:

- "Which feature flags are active in production?"
- "When was flag X enabled, and by whom?"
- "What's the blast radius if we toggle flag Y?"

`hecate-flag-audit` answers these **without mutating state**:

```bash
hecate-flag-audit list                  # All flags + status
hecate-flag-audit show <flag-name>      # Detail of one flag
hecate-flag-audit history <flag-name>   # Change history
hecate-flag-audit compare env1 env2     # Diff between environments
hecate-flag-audit report               # Generate compliance report
```

Read-only by design. No side effects. Safe to run in production at any time.

---

## CLI vs API vs Canvas

Hecate has three primary interfaces. Each has its own strengths:

| Task | CLI | API | Canvas |
|---|---|---|---|
| Bulk operations on many agents | ✅ `hecate agent list` | ✅ | ❌ Too slow |
| Quick scripted operations | ✅ | ⚠️ Requires HTTP client | ❌ |
| Real-time visual debugging | ❌ | ⚠️ Hard to read JSON | ✅ |
| Non-developer workflow editing | ❌ | ❌ | ✅ |
| CI/CD automation | ✅ | ✅ | ❌ |
| Interactive chat with agent | ✅ `hecate chat interactive` | ⚠️ | ❌ |
| Audit / compliance reports | ✅ `hecate-flag-audit` | ⚠️ | ❌ |
| Browser-based exploration | ❌ | ✅ Swagger UI | ✅ |

**Rule of thumb**:
- **CLI** for engineers and CI/CD (scriptable, fast, batch)
- **API** for application integration (programmatic, HTTP)
- **Canvas** for visual workflow design (non-developers)

---

## How the CLI talks to the backend

The CLI is a **client** to the Hecate backend. All state-changing operations are HTTP calls to the REST API:

```
hecate agent create --name "X" ...
        │
        │ HTTP POST /api/agents
        │ Authorization: Bearer $HECATE_API_KEYS
        ▼
   ┌─────────────┐
   │  Hecate API │
   │   backend   │
   └─────────────┘
```

This means:

- **CLI requires Hecate backend to be running** (except `hecate-migrate` which needs only the DB)
- **CLI auth is the same as API auth** (API keys, bearer tokens)
- **CLI output is the same as API output** (just formatted for the terminal)

CLI configuration (via `hecate config`):

```bash
hecate config set api-url http://localhost:8000
hecate config set api-key hcat_xxxxxxxxxxxx
hecate config set profile production
```

Stored in `~/.config/hecate/config.toml` (Linux) or `~/Library/Application Support/hecate/config.toml` (macOS).

---

## Plugin discovery

Plugins can contribute CLI subcommands. When a plugin of type `cli` is enabled, its subcommands appear under `hecate <plugin-name>`:

```bash
# Built-in
hecate agent list
hecate workflow validate

# From a custom plugin
hecate my-plugin run  # ← from a loaded plugin
```

Plugin CLI commands must:

1. Follow Typer conventions (same framework as built-in)
2. Be registered via the plugin manifest (`cli` field)
3. Be namespaced (e.g., `hecate my-plugin` not `hecate custom`)

This is how Hecate avoids CLI bloat — features ship as plugins without bloating the core.

---

## Installation

The three entry points are installed via `pip` / `uv`:

```bash
# Install Hecate (installs all three)
uv pip install -e ".[dev]"

# Or just the app (excludes flag-audit)
uv pip install hecate

# Verify
which hecate hecate-migrate hecate-flag-audit
hecate --version
hecate-migrate --version
```

In Docker images, all three are pre-installed at `/usr/local/bin/`.

---

## When to extend the CLI

You can add CLI subcommands in three ways:

1. **Plugin (recommended)**: write a Typer app, declare in plugin manifest with `cli` field. Distributed as a package.
2. **Fork**: clone the repo, modify `src/hecate/cli/`, submit a PR. Used for core-only features.
3. **Wrapper script**: write a shell / Python script that calls the API. Used for one-off automation.

For most extensions, **plugins** are the right answer.

---

## Common patterns

### Bulk operations

```bash
# Delete all agents matching a pattern
hecate agent list --format json | jq -r '.[] | select(.name | contains("test-")) | .id' | \
  xargs -I {} hecate agent delete {}

# Re-run all workflows for a tag
hecate workflow list --tag nightly | jq -r '.[].id' | \
  xargs -I {} hecate workflow run {}
```

### Interactive REPL

```bash
hecate chat interactive <agent-id>
> What's the difference between TCP and UDP?
Agent: TCP (Transmission Control Protocol) is connection-oriented...
> /clear    # Clear context
> /history  # Show messages so far
> /exit     # Exit
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
    curl -f http://localhost:8000/health
```

---

## What's NOT in the CLI

| Feature | Use instead |
|---|---|
| **Visual workflow design** | `web/` canvas |
| **Real-time observability** | Ops Center dashboard in canvas |
| **Multi-user collaboration** | Canvas (single-user CLI sessions) |
| **Admin policy editing** | Management API (no CLI yet) |
| **Backup / restore** | `POST /api/backups` (no CLI yet) — see [Backup Runbook](../operations/backup-restore.md) |

---

## Implementation references

- `src/hecate/cli/main.py` — main CLI app with subcommand registration
- `src/hecate/cli/commands/` — 16 subcommand modules (one per domain)
- `src/hecate/cli/migrate.py` — `hecate-migrate` entry point
- `src/hecate/cli/flag_audit.py` — `hecate-flag-audit` entry point
- `src/hecate/cli/config.py` — profile-based config
- `src/hecate/cli/client.py` — HTTP client used by CLI commands
- `src/hecate/cli/output.py` — terminal formatting (Rich-based)

## Related documents

- [CLI Reference](../reference/cli.md) — every command, subcommand, flag
- [How-to: Use the CLI](../how-to/cookbook.md) — recipes (CLI cookbook section)
- [Tutorial: Build Your First Agent](../tutorials/01-first-agent.md) — uses CLI extensively
- [Tools, MCP, and A2A](tools-and-mcp.md) — CLI for tool operations
- [Extension SPI & Plugin Architecture](../design/extension-architecture.md) — adding CLI subcommands via plugins
- [Architecture Center](../design/) — broader design context