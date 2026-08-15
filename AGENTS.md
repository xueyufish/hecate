# AGENTS.md — Hecate

## What this repo is

Hecate is an **enterprise-grade, multi-tenant, model-agnostic, MCP-first Agent platform** — supporting cloud SaaS deployment and self-hosted private deployment. Built with Python 3.12+, FastAPI, and SQLAlchemy 2.0 async.

## Commands

```bash
# Install (uses uv + venv at .venv/)
source .venv/bin/activate && uv pip install -e ".[dev]"

# Run all tests (1713 tests, takes ~6 min)
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

**Pre-commit hooks** run all 4 checks (ruff, ruff-format, mypy, pytest). pytest uses `scripts/smart-pytest.sh` which scopes tests to affected layers and skips for non-Python changes. Never use `--no-verify`.

## Architecture layers

```
engine/     → Zero external deps (no imports from services/, api/, models/); jsonschema is sole exception
services/   → Depends on models/, engine/ports (abstract interfaces only), and external libraries
api/        → Depends on services/ and models/; never imports engine/ directly
models/     → Pure data definitions (ORM + Pydantic); no business logic
core/       → Infrastructure: config (pydantic-settings), database (async SQLAlchemy), DI, rate limiting
```

**Layering violations to know about:**
- `engine/checkpoint.py` PostgresCheckpointStore imports from `models/` — legacy, do not replicate.
- `engine/temporal/run_worker.py` imports from `core/` — same.

**Engine `__init__.py` is empty** — import directly from submodules: `from hecate.engine.pregel import PregelRuntime`.

## Engine Extension Point Inventory

The engine layer defines these abstract interfaces (all in `src/hecate/engine/`):

| Extension Point | File | Abstract methods | InMemory impl |
|-----|------|-----------------|---------------|
| EnginePort | `ports.py` | llm_invoke, tool_execute, knowledge_query, checkpoint_save/load, conversation_load/save | — (services provide adapter) |
| Worker | `worker.py` | execute | AgentWorker in `workers/` |
| WorkerPool | `worker.py` | dispatch | DirectWorkerPool |
| CheckpointStore | `checkpoint.py` | save, load, list_checkpoints | InMemoryCheckpointStore |
| EventStore | `eventstore.py` | append, get_events, replay, get_version | InMemoryEventStore |
| ContextEngine | `context.py` | select_messages, compress, estimate_tokens | InMemoryContextEngine |
| SchedulerStrategy | `scheduler.py` | select_next, set_weights | FIFOScheduler |
| EvictionPolicy | `eviction.py` | should_evict, select_victim | NoEviction, SizeBasedEviction |
| OptimizationPass | `optimization.py` | optimize | DeadNodeElimination, ParallelBranchDetection |
| ConflictResolver | `temporal/conflict.py` | resolve | NoOpConflictResolver |
| PreLLMHook / PostLLMHook / PreToolHook / PostToolHook | `guardrail.py` | on_pre_llm_call / on_post_llm_call / on_pre_tool_call / on_post_tool_call | NoOp variants for each |
| RetryStrategy | `retry.py` | should_retry, get_backoff, with_config | NoRetryStrategy |

EnginePort also has 6 optional methods with defaults: `context_assemble`, `evidence_query`, `agent_execute`, `tool_execute_sandbox`, `workflow_execute`, `llm_invoke_structured`. `llm_invoke_structured` delegates to `llm_invoke` by default (yields a single `{"content", "tool_calls": None}` chunk); the production adapter overrides it to stream content and accumulate structured `tool_calls` for the chat graph tool loop.

**Integration status**: ContextEngine wired into LLMWorker via PregelRuntime execution_context (Phase 1). GuardrailHooks are Worker-level only, not PregelRuntime-level (P3). RetryStrategy integrated into PregelRuntime via RetryExecutor (P3).

## Key files (read these first on a new session)

| File | Purpose |
|------|---------|
| `docs/design/architecture.md` | Top-level architecture overview |
| `docs/design/engine-design.md` | Execution engine deep dive |
| `docs/design/concepts.md` | Core entity model and data design |
| `docs/design/adr/` | Architecture Decision Records (29 ADRs; see `docs/design/adr/INDEX.md` for topic index) |
| `src/hecate/engine/graph-dsl.schema.json` | Graph DSL JSON Schema (4 node types, 4 channel types) — bundled in package |
| `openspec/specs/` | 139 spec directories — the source of truth for each feature |
| `openspec/changes/archive/` | Completed OpenSpec changes |

> **Note**: Hecate's public strategic docs live at `docs/design/positioning.md` (vs competitors). All ADRs are at `docs/design/adr/INDEX.md` (topic-grouped) and `docs/design/adr/001-028*.md` (chronological). For the full docs map, start at `docs/README.md`.

## Gotchas and non-obvious facts

- **Python env**: uv + Python 3.12, venv at `.venv/`. Use `uv pip install`, not bare `pip install`.
- **Git**: GitHub Flow — all changes via PR to `main`. **`main` is a protected branch — DO NOT commit, amend, push, or apply edits directly to it.** Always create a feature branch first (`feat/xxx`, `fix/xxx`, `docs/xxx`, `chore/xxx`) via `git checkout -b <branch>` or `./scripts/opsx-flow.sh start <name>` and open a PR. CI runs on push and PR to `main`. Tag releases from `main` commits.
  - **Self-check before any write**: `git rev-parse --abbrev-ref HEAD` must NOT return `main`. If it does, `git checkout <branch>` or `git checkout -b <branch>` first.
  - **If you accidentally edited on main**: `git stash push -u -m "..."` then `git checkout -b docs/<topic>` (or `fix/<topic>` / `feat/<topic>`) then `git stash pop`. **Never** `git commit` on main, **never** `git reset --hard` on main to "undo" — the latter is unrecoverable without reflog archaeology.
  - **Worktrees bypass this naturally**: `opsx-flow.sh start` always creates `feat/<name>` (or reuses it). Never run OpenSpec work directly on `main`.
  - **Always confirm before `git push` to GitHub**: never push to `origin` (or any remote) without explicit user confirmation in chat. After CI checks pass locally, ask the user to approve the push before invoking `git push`, `git push --force-with-lease`, `./scripts/opsx-flow.sh push`, or any wrapper that calls push. The pre-push hook may still rebase and re-push after confirmation — that is fine. `--no-verify` on push requires explicit justification in chat. This applies to feature branches, not just `main`.
- **Pre-commit hook** (`scripts/prevent-main-commit.sh`, installed to `.git/hooks/pre-commit`): refuses any `git commit` while the current branch is `main`, preventing accidental commits to the protected branch. The hook prints the recovery recipe (`git stash` → `git checkout -b <topic>` → `git stash pop`). Bypass with `git commit --no-verify` only for explicit edge cases (e.g. release tagging from `main`). Install once per clone: `cp scripts/prevent-main-commit.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit`. Worktrees share hooks with the main repo, so installing in the main `.git/hooks/` covers all worktrees. The existing `scripts/pre-commit.sh` (CI checks) calls this one first, so installing it via `cp scripts/pre-commit.sh` automatically gets main-protection.
- **Pre-push hook** (`scripts/pre-push.sh`, installed to `.git/hooks/pre-push`): every `git push` automatically fetches `origin` and rebases against `origin/main` if the branch has fallen behind. If the rebase hits conflicts, the push is aborted and the hook prints resolution steps (`add → rebase --continue → re-push`). Install once per clone: `cp scripts/pre-push.sh .git/hooks/pre-push && chmod +x .git/hooks/pre-push`. Worktrees share hooks with the main repo, so installing in the main `.git/hooks/` covers all worktrees.
- **CheckpointModel** inherits `Base` (not `BaseModel`) — intentionally immutable, no `updated_at`/`deleted_at`.
- **AgentModel.model_config_db** — ORM column named `model_config` via `mapped_column("model_config", JSON)` to avoid Pydantic's `model_config` collision. CreateSchema uses `alias="model_config"`, ReadSchema uses `serialization_alias="model_config"`.
- **metadata_ alias** — 5 models use `metadata_` (Python) → `metadata` (SQL) to avoid SQLAlchemy's reserved `metadata` attribute. ReadSchema uses `Field(validation_alias="metadata_")`.
- **engine/command.py** is a re-export of `Command` from `types.py` — convenience import, not dead code.
- **compiler._detect_unreachable()** uses BFS from entry point; logs WARNING for unreachable nodes (does not raise).
- **ChannelManager.write()** silently skips unregistered channels (no error). **read()** raises KeyError for unregistered channels. **restore()** bypasses write semantics — directly sets `_value` field.
- **StreamMode.DEBUG** defined but not yielded in PregelRuntime (P3). StreamMode.MESSAGES is implemented.
- **PERSISTENT_TOPIC** is deprecated — auto-migrated to `topic` with `persistent: true` in graph_dsl.py.
- **mypy strict=true** but many error codes disabled in pyproject.toml — not truly strict.
- **pyright LSP** produces false positives for Python 3.12 StrEnum — safe to ignore these diagnostics.
- **Optional dependency groups** in pyproject.toml: `[llm]`, `[temporal]`, `[rag]`, `[security]`, `[tools]`, `[observability]`, `[mysql]`, `[scheduling]`, `[dev]`. Declare new packages in the right group.
- **Conftest location**: `tests/conftest.py` (not root). Single file, no per-directory conftests.

## Conventions

### Project workflow

- Feature IDs: `X.Y.Z` pattern (e.g., `1.3.1`, `9.4a`). Append letter suffixes — never renumber.
- **OpenSpec workflow is MANDATORY for ALL changes** — no exceptions. Every change MUST follow: `proposal → design → specs → tasks → implement → verify → archive`. Use `/opsx-propose` to create a change, then `/opsx-apply` to implement tasks, then run verification commands, then `/opsx-archive` to close. Never skip the propose step or implement outside an OpenSpec change directory. Mark tasks complete in `tasks.md` immediately.
- **OpenSpec commands MUST be triggered by the user manually** — the AI agent SHALL NOT automatically invoke `/opsx-explore`, `/opsx-propose`, `/opsx-apply`, `/opsx-archive`, or any other `/opsx-*` command. The agent may suggest running a command, but MUST wait for explicit user approval.
- **OpenSpec change file sync (worktree creation)** — `./scripts/opsx-flow.sh start <name>` (via `worktree-help.sh start`) automatically detects uncommitted OpenSpec change artifacts under `openspec/changes/<name>/` in the **main repo's working tree** and syncs them into the new worktree. This rescues the case where `/opsx-propose` was run in the main checkout by mistake (worktrees only inherit tracked files from the base branch). After the worktree is ready, clean up the main copy with `rm -rf openspec/changes/<name>`.
- **Correct OpenSpec invocation sequence**:
  1. (Optional, in main) `/opsx-explore` — initial scoping to decide the change name and high-level shape. Does not write OpenSpec artifacts, so it is safe to run in main.
  2. `./scripts/opsx-flow.sh start <change-name>` — creates worktree on `feat/<change-name>` branch.
  3. (Optional, in worktree) `/opsx-explore` — deep dive with file/code references to refine the design before proposing.
  4. Inside the worktree: `/opsx-propose <change-name>` — generates `proposal.md` / `design.md` / `specs/*.md` / `tasks.md` under `openspec/changes/<change-name>/`.
  5. Inside the worktree: `/opsx-apply <change-name>` — implements tasks from `tasks.md`.
  6. `./scripts/opsx-flow.sh push <change-name>` — pushes branch to origin.
  7. PR → merge → `/opsx-archive <change-name>` to close the change.
- Feature catalog: maintain P1→P5 priority ordering, update counts when features change.
- **Catalog & Roadmap sync is MANDATORY** — when archiving an OpenSpec change (`/opsx-archive`), the agent MUST check and update `docs/design/positioning.md` before performing the archive move. This includes: updating feature descriptions. If the user skips this step in the archive flow, the agent MUST still remind them after the archive completes.
- Run `ruff check` + `ruff format --check` + `mypy` + `pytest` before committing.

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

Standard Python naming elsewhere: `snake_case` for modules/functions, `PascalCase` for classes, `UPPER_SNAKE` for constants.

### Language

All **code** artifacts — Python/TypeScript code, docstrings, comments, inline annotations — **SHALL be written in English**.

**OpenSpec change artifacts** — `proposal.md`, `design.md`, `tasks.md`, and `specs/*.md` in `openspec/changes/` — **SHALL be written in Chinese by default** (prose content). Business terms, entity names, data table names, code classes/fields/properties, file paths, environment variables, and technology names (e.g. Docker, PostgreSQL, FastAPI, LangGraph) SHALL remain in English.

We may converse in Chinese. Code artifacts are always English; OpenSpec change documents default to Chinese unless explicitly noted otherwise.

## Testing

- `tests/` mirrors `src/hecate/` structure (`test_engine/`, `test_models/`, `test_api/`, `test_services/`).
- Single `conftest.py` at `tests/`: `db_session` (AsyncSession + auto-rollback), `setup_database` (autouse, create_all/drop_all per test), `client` (httpx AsyncClient with DI overrides).
- **Do NOT create separate engines in test files** — use `db_session` from conftest.
- `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` decorator needed.
- Database: in-memory SQLite (`sqlite+aiosqlite://`). Never connect to real PostgreSQL in unit tests.
- Engine tests use lightweight stub classes (`SimpleWorker`, `InterruptWorker`) instead of mocking frameworks.
- No factories — create models inline with `db_session.add()` + `await db_session.flush()`.
- ruff S101 (assert in tests) is expected — per-file-ignores in pyproject.toml handle it.
- **Integration tests** (tests that need ChannelManager, PregelRuntime, GraphCompiler, LLMService integration) must wait until the actual integration code is implemented — do not write integration tests for features that are interface-only.

## What to do / What not to do

- **Do** run `ruff check src/hecate/ tests/ && ruff format --check src/ tests/ && mypy src/ && python -m pytest tests/ -q` before committing.
- **Do** ensure **0 errors** locally before pushing to GitHub. If any check fails, fix it first.
- **Do** use `conftest.py`'s `db_session` fixture in all test files that need database access.
- **Do** add new Python packages to `pyproject.toml` dependencies immediately when installing locally. Never use a package in code without declaring it.
- **Do** write tests for new engine extension points: test the interface is not instantiable, test InMemory implementations, test edge cases. Do NOT write tests that reference integration points (ChannelManager, PregelRuntime, etc.) until those integration points actually exist.
- **Don't** renumber feature IDs — use letter suffixes.
- **Don't** commit PDF files or large binary assets.
- **Don't** add comments to code unless the logic is non-obvious.
- **Don't** use `as any`, `@ts-ignore` or equivalent type suppression.
- **Don't** import from `engine/` in `api/` — route through `services/` + `EnginePort`.
- **Don't** use `git commit --no-verify` to skip pre-commit hooks.
- **Don't** push to GitHub without explicit user confirmation. After local CI passes, ask the user to approve the push before running `git push` (or any wrapper such as `./scripts/opsx-flow.sh push`). The pre-push hook may still rebase and re-push after the user approves — that is fine and does not require a second confirmation.
- **Don't** push directly without letting the pre-push hook rebase against `origin/main`. If you need to skip the hook for a force-push or special case, use `git push --no-verify` and explain why.
- **Don't** assume test failures are "pre-existing" without investigating.
- **Don't** delegate OpenSpec implementation tasks (`/opsx-apply`) to background agents. Implementation requires full design/specs/tasks context which causes background agent timeouts. Always implement directly as the main agent.
