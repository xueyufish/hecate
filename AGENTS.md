# AGENTS.md — Hecate

## What this repo is

Hecate is an **enterprise-grade, multi-tenant, model-agnostic, MCP-first Agent platform** — supporting cloud SaaS deployment and self-hosted private deployment. Built with Python 3.12+, FastAPI, and SQLAlchemy 2.0 async.

## Local development setup

Hecate uses [uv](https://docs.astral.sh/uv/) with the venv at `.venv/`. The shipped [`.envrc`](.envrc) auto-creates and activates `.venv` on every `cd` into the repo or a worktree (including ones created by `./scripts/worktree-help.sh start`). One-time per machine: install direnv, hook it into your shell, run `direnv allow` — exact commands are in the `.envrc` header. Without direnv, use the manual install in `## Commands`.

## Commands

```bash
# Install (uv + venv at .venv/; --prerelease=allow needed while fastmcp 4.x is beta-only)
source .venv/bin/activate && uv pip install --prerelease=allow -e ".[dev]"

# Run all tests (full suite, several minutes)
python -m pytest tests/ -q

# Run a single test file or function
python -m pytest tests/test_runtime/test_pregel.py -v
python -m pytest tests/test_runtime/test_pregel.py::test_linear_execution -v

# Verify before committing (run ALL of these)
ruff check src/hecate/ tests/
ruff format --check src/ tests/
mypy src/
python -m pytest tests/ -q

# Start infrastructure (PostgreSQL 16, Qdrant, MinIO, Temporal)
docker compose -f docker/docker-compose.yml up -d

# Run database migrations (requires PostgreSQL running)
alembic upgrade head

# Run the application
uvicorn hecate.main:app --reload
```

**Pre-commit hooks** run ruff, ruff-format, commitizen (commit-message check), mypy, and pytest. pytest is scoped to affected layers by `scripts/smart-pytest.sh` and skipped for non-Python changes. Never use `--no-verify`.

## Architecture

Modular monolith after Phase R: six domain directories (`runtime/`, `tools/`, `enterprise/`, `channel/`, `studio/`, `ops/`), two cross-cutting layers (`core/` infrastructure + composition root, `models/` data contracts), and a uv workspace of extracted packages under `packages/`.

- Runtime domain deep dive, self-sufficiency invariant, and extension-point inventory: [`src/hecate/runtime/AGENTS.md`](src/hecate/runtime/AGENTS.md)
- Docs map (per-section READMEs): [`docs/design/README.md`](docs/design/README.md) — architecture, engine design, concepts, ADR index
- Engine boundary start: `src/hecate/runtime/ports.py` (RuntimePort)
- Engine/model gotchas (ORM aliases, ChannelManager semantics, StreamMode, PERSISTENT_TOPIC migration): [`docs/gotchas.md`](docs/gotchas.md)

## Gotchas and non-obvious facts

- **Python env**: uv + Python 3.12, venv at `.venv/`. Use `uv pip install`, not bare `pip install`.
- **Git**: GitHub Flow; all changes via PR. **`main` is a protected branch — never commit, amend, push, or edit directly on it.** Self-check before any write: `git rev-parse --abbrev-ref HEAD` must not return `main`; if it does, `git checkout -b <branch>` first (`feat/`, `fix/`, `docs/`, `chore/`). If you accidentally edited on main: `git stash push -u -m "..."` → `git checkout -b <topic>` → `git stash pop`; never `git reset --hard` on main. CI runs on push and PR to `main`; tag releases from `main` commits.
- **Merge commits are disabled at the repo level** — only "Rebase and merge" or "Squash and merge" exist on GitHub; prefer `git rebase origin/main` over `git merge origin/main` locally so `main` stays linear.
- **Push requires explicit user confirmation in chat** — any `git push`, `--force-with-lease`, or wrapper (`./scripts/opsx-flow.sh push`). After approval, the pre-push hook may rebase and re-push without a second confirmation. `--no-verify` (push or commit) needs explicit justification.
- **Git hooks** (install once per clone; worktrees share `.git/hooks/`): `cp scripts/pre-commit.sh .git/hooks/pre-commit` — runs the checks and refuses commits on `main` (prints its own recovery recipe). `cp scripts/pre-push.sh .git/hooks/pre-push` — rebases onto `origin/main` before push; aborts with printed steps on conflict.

## Conventions

### Project workflow

- Feature IDs: `X.Y.Z` pattern (e.g., `1.3.1`, `9.4a`). Append letter suffixes — never renumber.
- **OpenSpec workflow is MANDATORY for ALL changes**: `proposal → design → specs → tasks → implement → verify → archive`. Invocation: (optional `/opsx-explore`) → `./scripts/opsx-flow.sh start <name>` → `/opsx-propose <name>` → `/opsx-apply <name>` → `./scripts/opsx-flow.sh push <name>` → PR → merge → `/opsx-archive <name>` — the script owns branch/worktree setup and pre-push rebase; the commands own artifacts. Never implement outside a change directory; mark tasks complete in `tasks.md` immediately.
- **`/opsx-*` commands are user-triggered only** — the agent may suggest but must wait for explicit approval.
- **On `/opsx-archive`**: check/update `docs/design/positioning.md` (feature descriptions, P1→P5 catalog) before the archive move; also re-evaluate AGENTS.md for rules to prune. Remind the user afterwards if skipped.

### Commits and PRs

- **Commit format is Conventional Commits** — `<type>(<scope>): <subject>`. Valid `type`: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`, `build`, `ci`. `subject` is imperative mood, no trailing period, ≤ 72 chars. Body and footer are free-form but must include a `Refs:` or `Closes:` trailer when the commit closes an issue or PR. Enforced by commitizen (already in the pre-commit config); bad-format commits are refused, not auto-fixed.
- **One PR, one purpose** — keep PRs focused on a single concern. If a branch carries independent changes (e.g., a refactor plus a feature), split before opening the PR or use stacked PRs. This pairs with the rebase-merge default: history should read as a sequence of independent changes, not one mega-squash.
- **Never bypass hooks with `--no-verify`** (commit or push). If a hook fails, fix the underlying issue — do not silence the gate. The only named exception is `./scripts/opsx-flow.sh push` force-pushing a synthesized squash commit when the pre-push rebase blocks on a known-good base; that path still requires explicit user approval per the Git rule above.
- **AGENTS.md is pruned, not grown** — every rule has a cost: each line loads into every session. If a rule is already standard Python / ruff / mypy / git behavior, remove it from this file. If a rule is no longer load-bearing, delete it. Re-evaluate on every `/opsx-archive`.

### Coding rules

Enforced mechanically by ruff (E/F/I/N/W/UP/B/SIM) and mypy — see `pyproject.toml`; don't restate those rules here. Project-specific:

- Docstrings in English on all modules, public classes, and public methods. Private (`_` prefix) exempt when self-explanatory.
- Inline comments: only for non-obvious logic, explain **why** not **what**.
- Declare every new Python package in `pyproject.toml` (correct optional group) at install time — never use an undeclared package in code.

### Naming

| Category | Convention | Example |
|----------|-----------|---------|
| SQLAlchemy models | `XxxModel` | `AgentModel` |
| Pydantic schemas | `XxxCreateSchema` / `XxxUpdateSchema` / `XxxReadSchema` | `AgentCreateSchema` |
| **Cross-layer boundary port** | `XxxPort` — reserved for runtime↔domain hexagonal seams only | `RuntimePort`, `AgentExecutionPort` |
| **Runtime-internal extension point** | Plain noun + `abc.ABC`; no `Port` / `Base` / `ABC` / `Abstract` / `I` marker | `Worker`, `EventStore`, `RetryStrategy` |
| **Test doubles** | `StubXxx` | `StubRuntimePort` |
| **Default implementations** | `HecateXxxAdapter` (production RuntimePort impl: `_ProductionRuntimePort` in `core/composition/runtime_port_adapter.py`) | — |
| **Vendor implementations** | `<Vendor>XxxAdapter` | `EmailNotificationAdapter` |

### Language and docs

- Code artifacts (Python/TypeScript, docstrings, comments, annotations) are always English. OpenSpec change artifacts under `openspec/changes/` default to Chinese prose; business terms, identifiers, paths, env vars, and technology names stay English.
- No specific numbers or dates as descriptive markers in `README.md` / `docs/**` — full rule and exemptions: [`docs/design/writing-style.md`](docs/design/writing-style.md).

## Testing

Conventions (fixtures, in-memory SQLite, stub classes, no factories): [`tests/AGENTS.md`](tests/AGENTS.md).

## What to do / What not to do

- **Do** reach **0 errors** on all four verification checks before pushing.
- **Don't** commit PDF files or large binary assets.
- **Don't** use `as any`, `@ts-ignore`, or equivalent type suppression.
- **Don't** assume test failures are "pre-existing" without investigating.
- **Don't** delegate `/opsx-apply` implementation to background agents (full design/specs/tasks context causes timeouts); implement directly as the main agent.
