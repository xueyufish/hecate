# AGENTS.md — Hecate

## What this repo is

Hecate is an **enterprise-grade, multi-tenant, model-agnostic, MCP-first Agent platform** — supporting cloud SaaS deployment and self-hosted private deployment. Built with Python 3.12+, FastAPI, and SQLAlchemy 2.0 async.

## Local development setup

Hecate uses [uv](https://docs.astral.sh/uv/) with the venv at `.venv/`. The shipped [`.envrc`](.envrc) auto-creates and activates `.venv` on every `cd` into the repo or a worktree (including ones created by `./scripts/worktree-help.sh start`). One-time per machine: install direnv, hook it into your shell, run `direnv allow` — exact commands are in the `.envrc` header. Without direnv, use the manual install in `## Commands`.

## Commands

```bash
# Install the locked development workspace into this worktree's .venv
bash scripts/dev.sh sync
bash scripts/dev.sh hooks install   # existing copied hooks: add --replace (keeps backups)
bash scripts/dev.sh doctor

# Run all tests (full suite, several minutes)
python -m pytest tests/ -q

# Run a single test file or function
python -m pytest tests/test_runtime/test_pregel.py -v
python -m pytest tests/test_runtime/test_pregel.py::test_linear_execution -v

# Verify before pushing (run ALL of these)
bash scripts/dev.sh check all
bash scripts/dev.sh test <affected dirs/files> -q   # full suite and platform matrix run in CI

# Start infrastructure (PostgreSQL 16, Qdrant, MinIO, Temporal)
docker compose -f docker/docker-compose.yml up -d

# Run database migrations (requires PostgreSQL running)
alembic upgrade head

# Run the application
uvicorn hecate.main:app --reload
```

**Git hooks are split by stage**: pre-commit runs fast checks only (ruff check + format, seconds); commit-msg validates the message via commitizen; pre-push runs the shared type check on `src/ packages/` and lets CI be the single source of truth for tests — the local gate deliberately does not run pytest, since running it twice (local + CI) wasted more time than it caught. Use `pytest --testmon` interactively for fast inner-loop feedback on just the affected tests (see "Inner-loop testing" below). Never use `--no-verify`.

## Architecture

Modular monolith after Phase R: domain directories (`runtime/`, `tools/`, `enterprise/`, `channel/`, `studio/`, `ops/`) plus the `execution/` control-plane boundary and language-neutral `contracts/` schemas, two cross-cutting layers (`core/` infrastructure + composition root, `models/` data contracts), and a uv workspace of extracted packages under `packages/`. The Pregel runtime kernel lives in `packages/hecate-runtime`; `src/hecate/runtime/` is a compatibility-forwarding shim layer.

- Runtime domain deep dive, self-sufficiency invariant, and extension-point inventory: [`packages/hecate-runtime/AGENTS.md`](packages/hecate-runtime/AGENTS.md)
- Docs map (per-section READMEs): [`docs/design/README.md`](docs/design/README.md) — architecture, engine design, concepts, ADR index
- Engine boundary start: `packages/hecate-runtime/src/hecate_runtime/ports.py` (RuntimePort)
- Engine/model gotchas (ORM aliases, ChannelManager semantics, StreamMode, PERSISTENT_TOPIC migration): [`docs/gotchas.md`](docs/gotchas.md)

## Gotchas and non-obvious facts

- **Python env**: uv + Python 3.12, venv at `.venv/`. Use `bash scripts/dev.sh sync` for the declared development environment; package dependencies belong in `pyproject.toml`.
- **Git**: GitHub Flow; all changes via PR. **`main` is a protected branch — never commit, amend, push, or edit directly on it.** Self-check before any write: `git rev-parse --abbrev-ref HEAD` must not return `main`; if it does, `git checkout -b <branch>` first (`feat/`, `fix/`, `docs/`, `chore/`). If you accidentally edited on main: `git stash push -u -m "..."` → `git checkout -b <topic>` → `git stash pop`; never `git reset --hard` on main. CI runs on push and PR to `main`; tag releases from `main` commits.
- **Merge commits are disabled at the repo level** — only "Rebase and merge" or "Squash and merge" exist on GitHub; prefer `git rebase origin/main` over `git merge origin/main` locally so `main` stays linear.
- **Push requires explicit user confirmation in chat** — any `git push`, `--force-with-lease`, or wrapper (`./scripts/opsx-flow.sh push`). After approval, the pre-push hook may rebase and re-push without a second confirmation. `--no-verify` (push or commit) needs explicit justification.
- **Git hooks**: install with `bash scripts/dev.sh hooks install`; use `--replace` to preserve and migrate existing hooks. Shared dispatchers execute the current worktree's tracked scripts and interpreter; `hooks check` verifies installation. Pre-push rebases onto `origin/main`, then runs the shared type check. Full pytest runs in CI.

## Conventions

### Project workflow

- Feature IDs: `X.Y.Z` pattern (e.g., `1.3.1`, `9.4a`). Append letter suffixes — never renumber.
- **Develop directly by default.** Do not invoke OpenSpec commands/skills or create change artifacts for ordinary requests, regardless of size, risk, APIs, security/tenant isolation, runtime semantics, architecture, CI/release gates, or uncertainty. Start an OpenSpec action only when the user manually invokes its slash command, named skill, or explicitly requests execution of its CLI command; mentioning OpenSpec, asking for a plan, an existing change, or the Agent's risk assessment is not a trigger. Ordinary work may read and update existing specs/docs directly. Keep affected tests, shared checks, CI, PR review, and actual verification results. See [the workflow guide](docs/how-to/openspec-workflow.md).
- **Work by coherent goal in an isolated worktree:** `./scripts/opsx-flow.sh start <name>` preserves checked-in skills by default and does not activate OpenSpec. Related tasks can share a plan and logical commits; independent purposes still need separate PRs. Investigate uncertainty, ask about unresolved material requirements or compatibility/security choices, and resolve routine implementation failures within scope.
- **Manual OpenSpec follows the selected action and schema.** Propose creates planning artifacts only; apply implements and verifies existing tasks; sync updates main specs; archive checks completion and synchronizes deltas before archiving; explore stays read-only except requested planning capture. Do not automatically advance to another action unless the user explicitly requests it or a command requires that substep (such as archive's sync). Explicitly requested action sequences may run consecutively; ordinary implementation authorization does not imply OpenSpec activation. Push still requires explicit chat confirmation.
- **When archiving a manually selected change:** preserve artifact/task and spec-sync checks; report pending CI/review/merge separately. Review `docs/design/positioning.md` when positioning/catalog text changed and prune `AGENTS.md` when rules changed. Archive does not authorize push, merge, publishing, or worktree deletion.

### Commits and PRs

- **Commit format is Conventional Commits** — `<type>(<scope>): <subject>`. Valid `type`: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`, `build`, `ci`. `subject` is imperative mood, no trailing period, ≤ 72 chars. Body and footer are free-form but must include a `Refs:` or `Closes:` trailer when the commit closes an issue or PR. Enforced by commitizen (via the commit-msg hook); bad-format commits are refused, not auto-fixed.
- **Merge queue** — `main` uses GitHub's merge queue; PR merges go through `merge_group` so the latest `main` tip is re-checked against each PR before landing. The `ci.yml` workflow listens on `merge_group` to satisfy this.
- **One PR, one purpose** — keep PRs focused on a single concern. If a branch carries independent changes (e.g., a refactor plus a feature), split before opening the PR or use stacked PRs. This pairs with the rebase-merge default: history should read as a sequence of independent changes, not one mega-squash.
- **Never bypass hooks with `--no-verify`** (commit or push). If a hook fails, fix the underlying issue — do not silence the gate. The only named exception is `./scripts/opsx-flow.sh push` force-pushing a synthesized squash commit when the pre-push rebase blocks on a known-good base; that path still requires explicit user approval per the Git rule above.
- **AGENTS.md is pruned, not grown** — every rule has a cost: each line loads into every session. If a rule is already standard Python / ruff / mypy / git behavior, remove it from this file. If a rule is no longer load-bearing, delete it. Re-evaluate on every `/opsx-archive`.

### Coding rules

Enforced mechanically by ruff (E/F/I/N/W/UP/B/SIM) and mypy — see `pyproject.toml`; don't restate those rules here. Project-specific:

- Docstrings in English on all modules, public classes, and public methods. Private (`_` prefix) exempt when self-explanatory.
- Inline comments: only for non-obvious logic, explain **why** not **what**.
- Declare every new Python package in `pyproject.toml` (correct optional group) at install time — never use an undeclared package in code.
- No new runtime extension points (ABC/Protocol) without a second implementation or a named consumer — name the implementation or consumer in the PR or an explicitly requested proposal (`runtime-pluggability` spec).

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

### Inner-loop testing (optional, recommended)

For quick feedback while developing — *not* a gate — use `pytest --testmon` (already in the dev extra). It records which source files each test touches and re-runs only the affected tests on subsequent invocations. First run is full-suite; later runs typically take seconds.

```bash
# First run on a checkout: builds .testmondata
pytest --testmon

# Subsequent runs after editing src/hecate/runtime/compaction.py:
pytest --testmon                 # only the tests touching that file
pytest --testmon tests/test_runtime/test_compaction.py   # scoped further
```

`--testmon` is **not** part of the local gate; CI runs the full suite. It exists to make the dev loop fast without weakening the safety net.

Conventions (fixtures, in-memory SQLite, stub classes, no factories): [`tests/AGENTS.md`](tests/AGENTS.md).

## What to do / What not to do

- **Do** reach **0 errors** on all required verification checks before pushing (pytest scoped to the tests your change touches locally — the full suite is CI's gate, not a local pre-push step).
- **Don't** commit PDF files or large binary assets.
- **Don't** use `as any`, `@ts-ignore`, or equivalent type suppression.
- **Don't** assume test failures are "pre-existing" without investigating.
- **Don't** delegate `/opsx-apply` implementation to background agents (full design/specs/tasks context causes timeouts); implement directly as the main agent.
