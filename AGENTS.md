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
python -m pytest tests/test_engine/test_pregel.py -v
python -m pytest tests/test_engine/test_pregel.py::test_linear_execution -v

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

## Architecture layers

```
engine/     → Zero external deps: no imports from services/, api/, models/, core/ at module
              level; jsonschema is the sole external exception. Exactly three modules carry
              cross-layer refs, all TYPE_CHECKING-only or lazy inside function/method bodies:
              pregel.py (services.observability logfold/loginvariants/logpolicy,
              services.temporal.conflict), tool_access.py (services.tool.shell_analysis),
              workers/coordinator_worker.py (orchestrator_validator, workflow.templates).
              Do not add more — guarded by tests/test_engine/test_runtime_self_sufficiency.py
              (Phase 0 gate for the future hecate-runtime wheel). Every other module holds
              the invariant, including middleware_factory.py (stays in engine/ deliberately).
services/   → Depends on models/, engine/ports (abstract interfaces only), and external libraries
api/        → Depends on services/ and models/. A few handlers import engine types for
              compile-time signatures (v1/chat, v1/agents, management/{sessions, replay,
              conversations, collaboration_patterns}) — tolerated legacy; do not add new
              ones. If you need an engine type, expose it via services/ first.
models/     → Pure data definitions (ORM + Pydantic); no business logic
core/       → Infrastructure: config (pydantic-settings), database (async SQLAlchemy), DI, rate limiting
```

Historical violations (`engine/temporal/`, `engine/checkpoint.py` PostgresCheckpointStore) are gone — removed in PR0.3 and the #89 audit cleanup.

**Engine `__init__.py` is empty** — import directly from submodules: `from hecate.engine.pregel import PregelRuntime`.

## Engine Extension Point Inventory

| Extension point | File | Default impl |
|-----|------|--------------|
| RuntimePort | `engine/ports.py` | `StubRuntimePort` (test double); production: `services/orchestration/runtime_port_adapter.py::create_runtime_port` |
| Worker / WorkerPool | `engine/worker.py` | `AgentWorker` / `DirectWorkerPool` |
| CheckpointStore | `engine/checkpoint.py` | `InMemoryCheckpointStore` |
| EventStore | `engine/eventstore.py` | `InMemoryEventStore` |
| ContextEngine | `engine/context.py` | `InMemoryContextEngine` |
| SchedulerStrategy | `engine/scheduler.py` | `FIFOScheduler` |
| EvictionPolicy | `engine/eviction.py` | `NoEviction`, `SizeBasedEviction` |
| OptimizationPass | `engine/optimization.py` | `DeadNodeElimination`, `ParallelBranchDetection` |
| Guardrail hooks (Pre/Post × LLM/Tool) | `engine/guardrail.py` | `NoOp*Hook` variants |
| MiddlewareChain | `engine/middleware.py` | `middleware_factory.py` builders; legacy hooks via `middleware_adapters.py` |
| MonotonicDenialTracker (concrete dataclass) | `engine/monotonic_denials.py` | per-session, wired via `services/security/guardrail_assembly.py` |
| RetryStrategy | `engine/retry.py` | `NoRetryStrategy` |
| ConflictResolver (concrete class) | `services/temporal/conflict.py` | strategies via `ConflictStrategy` enum |
| Shell analysis (module functions, no class) | `services/tool/shell_analysis.py` | feeds `engine/tool_access.py` content-aware gating |

RuntimePort defines 9 abstract methods (llm_invoke, tool_execute, knowledge_query, checkpoint_save/load, conversation_load/save, create_span, end_span) plus 6 optional defaults: `context_assemble`, `evidence_query`, `agent_execute`, `tool_execute_sandbox`, `workflow_execute`, `llm_invoke_structured` (the production adapter overrides the last to stream structured `tool_calls`).

Wired today: ContextEngine (PregelRuntime execution_context), guardrail hooks + middleware chains on both the Pregel path and the `api/v1/chat.py` direct tool loop (assembled by `services/security/guardrail_assembly.py`), and RetryStrategy via RetryExecutor.

## Key files (read these first on a new session)

| File | Purpose |
|------|---------|
| `docs/design/architecture.md` | Top-level architecture overview |
| `docs/design/engine-design.md` | Execution engine deep dive |
| `docs/design/concepts.md` | Core entity model and data design |
| `docs/design/adr/INDEX.md` | ADRs, topic-grouped (`001-032*.md` chronological) |
| `src/hecate/engine/ports.py` | RuntimePort boundary — start here for the engine/service seam |
| `src/hecate/services/security/guardrail_assembly.py` | Hook + middleware wiring entry point |
| `src/hecate/engine/graph-dsl.schema.json` | Graph DSL JSON Schema (10 node types, 4 channel types) |
| `openspec/specs/` | Spec directories — source of truth per feature |
| `openspec/changes/archive/` | Completed OpenSpec changes |

Strategic positioning lives at `docs/design/positioning.md`; the docs map starts at `docs/design/README.md` (per-section READMEs; there is no top-level `docs/README.md`).

## Gotchas and non-obvious facts

- **Python env**: uv + Python 3.12, venv at `.venv/`. Use `uv pip install`, not bare `pip install`.
- **Git**: GitHub Flow; all changes via PR. **`main` is a protected branch — never commit, amend, push, or edit directly on it.** Self-check before any write: `git rev-parse --abbrev-ref HEAD` must not return `main`; if it does, `git checkout -b <branch>` first (`feat/`, `fix/`, `docs/`, `chore/`). If you accidentally edited on main: `git stash push -u -m "..."` → `git checkout -b <topic>` → `git stash pop`; never `git reset --hard` on main. CI runs on push and PR to `main`; tag releases from `main` commits.
- **Push requires explicit user confirmation in chat** — any `git push`, `--force-with-lease`, or wrapper (`./scripts/opsx-flow.sh push`). After approval, the pre-push hook may rebase and re-push without a second confirmation. `--no-verify` (push or commit) needs explicit justification.
- **Git hooks** (install once per clone; worktrees share `.git/hooks/`): `cp scripts/pre-commit.sh .git/hooks/pre-commit` — runs the checks and refuses commits on `main` (prints its own recovery recipe). `cp scripts/pre-push.sh .git/hooks/pre-push` — rebases onto `origin/main` before push; aborts with printed steps on conflict.
- **AgentModel.model_config_db** — ORM column `model_config` via `mapped_column("model_config", JSON)` (avoids Pydantic `model_config` collision). CreateSchema `alias="model_config"`, ReadSchema `serialization_alias="model_config"`.
- **metadata_ alias** — 11 models across 10 modules map `metadata_` (Python) → `metadata` (SQL); ReadSchema uses `Field(validation_alias="metadata_")`.
- **engine/command.py** is a re-export of `Command` from `types.py` — convenience import, not dead code.
- **compiler._detect_unreachable()** uses BFS from entry; logs WARNING for unreachable nodes (does not raise).
- **ChannelManager**: `write()` silently skips unregistered channels; `read()` raises KeyError (`ChannelNotFoundError`); `restore()` bypasses write semantics and sets `_value` directly.
- **StreamMode** has only VALUES, UPDATES, MESSAGES — DEBUG no longer exists. MESSAGES is the SSE streaming mode.
- **PERSISTENT_TOPIC** channel type is deprecated — auto-migrated to `topic` + `persistent: true` by `services/workflow/graph_dsl.py`.
- **mypy** `strict=true` but many error codes disabled in pyproject.toml — not truly strict.
- **Optional dependency groups** in pyproject.toml: `[llm] [temporal] [rag] [security] [tools] [redis] [observability] [mysql] [scheduling] [dev]` — declare new packages in the right group.
- **Conftest**: shared fixtures in `tests/conftest.py`; two per-directory conftests exist (`test_browser`, `test_session_state` — fakeredis). Don't add more unless a fixture can't be shared.

## Conventions

### Project workflow

- Feature IDs: `X.Y.Z` pattern (e.g., `1.3.1`, `9.4a`). Append letter suffixes — never renumber.
- **OpenSpec workflow is MANDATORY for ALL changes**: `proposal → design → specs → tasks → implement → verify → archive`, driven by `/opsx-propose` → `/opsx-apply` → verification commands → `/opsx-archive`. Never implement outside a change directory; mark tasks complete in `tasks.md` immediately.
- **`/opsx-*` commands are user-triggered only** — the agent may suggest but must wait for explicit approval.
- Invocation sequence: (optional `/opsx-explore` in main — writes no artifacts, safe there) → `./scripts/opsx-flow.sh start <name>` (creates the `feat/<name>` worktree; also syncs uncommitted `openspec/changes/<name>/` artifacts from the main checkout — `rm -rf` that copy afterwards) → `/opsx-propose <name>` → (optional deeper `/opsx-explore`) → `/opsx-apply <name>` → `./scripts/opsx-flow.sh push <name>` → PR → merge → `/opsx-archive <name>`.
- **On `/opsx-archive`**: check/update `docs/design/positioning.md` (feature descriptions, P1→P5 catalog) before the archive move; remind the user afterwards if skipped.

### Coding rules (enforced by ruff E/F/I/N/W/UP/B/SIM)

- `from __future__ import annotations` at top of every file.
- All public functions/methods require type annotations.
- Mutable defaults: use `None`, never `[]` or `{}`.
- `except:` without exception type prohibited. Always `except X as x`.
- `assert` prohibited in production code.
- No commented-out code — delete entirely.
- f-strings only; no `+`/`+=` for string concatenation in loops.
- Docstrings in English on all modules, public classes, and public methods. Private (`_` prefix) exempt when self-explanatory.
- Inline comments: only for non-obvious logic, explain **why** not **what**.

### Naming

| Category | Convention | Example |
|----------|-----------|---------|
| SQLAlchemy models | `XxxModel` | `AgentModel` |
| Pydantic schemas | `XxxCreateSchema` / `XxxUpdateSchema` / `XxxReadSchema` | `AgentCreateSchema` |
| **Cross-layer boundary port** | `XxxPort` — reserved for engine↔services hexagonal seams only | `RuntimePort`, `AgentExecutionPort` |
| **Engine-internal extension point** | Plain noun + `abc.ABC`; no `Port` / `Base` / `ABC` / `Abstract` / `I` marker (`abc.ABC` in the bases already marks abstractness) | `Worker`, `EventStore`, `ContextEngine`, `RetryStrategy` |
| **Test doubles** | `StubXxx` | `StubRuntimePort` |
| **Default implementations** | `HecateXxxAdapter` (current production RuntimePort impl: `_ProductionRuntimePort` in `services/orchestration/runtime_port_adapter.py`) | — |
| **Vendor implementations** | `<Vendor>XxxAdapter` | `EmailNotificationAdapter`, `WebhookNotificationAdapter` |

Standard Python naming elsewhere: `snake_case` for modules/functions, `PascalCase` for classes, `UPPER_SNAKE` for constants.

### Language

Code artifacts — Python/TypeScript code, docstrings, comments, inline annotations — are always English. OpenSpec change artifacts (`proposal.md`, `design.md`, `tasks.md`, `specs/*.md` under `openspec/changes/`) default to Chinese prose; business terms, entity/table names, code identifiers, file paths, env vars, and technology names stay English.

### Documentation — no specific numbers or dates

`README.md` and `docs/**` (temporarily exempt: `docs/design/adr/`, `docs/features/`) must not contain specific counts or dates as descriptive/marketing markers — e.g. "32 ADRs", "1713 tests", "shipped 2026-08-22". Use vague qualifiers (`many`, `several`, `recent`) or drop the number. Allowed exceptions: functional version requirements (`Python 3.12+`, `SQLAlchemy 2.0`), HTTP status codes / port numbers / file sizes in commands, dates inside JSON examples and `openspec/` archive folder names, project-internal IDs (`P1`–`P5`, feature codes like `13.5`), quantitative hardware minimums in install instructions, and factual observations in `docs/research/`. Rationale: counts and dates go stale.

## Testing

- `tests/` mirrors `src/hecate/` (`test_engine/`, `test_models/`, `test_api/`, `test_services/`).
- Shared fixtures in `tests/conftest.py`: `db_session` (AsyncSession + auto-rollback), `setup_database` (autouse, create_all/drop_all per test), `client` (httpx AsyncClient + DI overrides). Use `db_session` in all DB tests — never create separate engines in test files.
- `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` needed. Database is in-memory SQLite (`sqlite+aiosqlite://`); never connect to real PostgreSQL in unit tests.
- Engine tests use lightweight stub classes (`SimpleWorker`, `InterruptWorker`), not mocking frameworks. No factories — create models inline with `db_session.add()` + `await db_session.flush()`.
- ruff S101 (assert in tests) is expected — per-file-ignores in pyproject.toml handle it.
- New engine extension points: test that the interface is not instantiable, test the default impl, test edge cases. Do NOT write tests referencing integration points (ChannelManager, PregelRuntime, GraphCompiler, LLMService) until those integrations actually exist.

## What to do / What not to do

- **Do** declare every new Python package in `pyproject.toml` (correct optional group) at install time — never use an undeclared package in code.
- **Do** reach **0 errors** on all four verification checks before pushing.
- **Don't** commit PDF files or large binary assets.
- **Don't** use `as any`, `@ts-ignore`, or equivalent type suppression.
- **Don't** assume test failures are "pre-existing" without investigating.
- **Don't** delegate `/opsx-apply` implementation to background agents (full design/specs/tasks context causes timeouts); implement directly as the main agent.
