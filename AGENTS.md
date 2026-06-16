# AGENTS.md — Hecate

## What this repo is

Hecate is an **enterprise-grade, self-hosted, model-agnostic, MCP-first Agent platform** built with Python 3.12+, FastAPI, and SQLAlchemy 2.0 async. P1 (19 features) and P2 (63 features) are 100% complete. P3 in progress (28/89).

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

## Engine ABC inventory

The engine layer defines these abstract interfaces (all in `src/hecate/engine/`):

| ABC | File | Abstract methods | InMemory impl |
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

EnginePort also has 4 optional methods with defaults: `context_assemble`, `evidence_query`, `agent_execute`, `tool_execute_sandbox`.

**Integration status**: ContextEngine is defined but NOT wired into PregelRuntime (P3). GuardrailHooks are Worker-level only, not PregelRuntime-level (P3).

## Key files (read these first on a new session)

| File | Purpose |
|------|---------|
| `docs/design/architecture.md` | Top-level architecture overview |
| `docs/design/engine-design.md` | Execution engine deep dive |
| `docs/design/concepts.md` | Core entity model and data design |
| `docs/design/adr/` | Architecture Decision Records (10 ADRs) |
| `src/hecate/engine/graph-dsl.schema.json` | Graph DSL JSON Schema (4 node types, 4 channel types) — bundled in package |
| `openspec/specs/` | 86 spec directories — the source of truth for each feature |
| `openspec/changes/archive/` | Completed OpenSpec changes |

> **Note**: `docs/features/feature-catalog.md` and `docs/features/roadmap.md` are local-only files (gitignored, not in the public repo). They contain competitive analysis and detailed feature tracking. If you need access, ask the maintainer.

## Gotchas and non-obvious facts

- **Python env**: uv + Python 3.12, venv at `.venv/`. Use `uv pip install`, not bare `pip install`.
- **Git**: development branch is `f_dev`. CI runs on push to `main` and `f_dev`. Public releases are squash-merged from `f_dev` to `main`.
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
- Feature catalog: maintain P1→P5 priority ordering, update counts when features change.
- **Catalog & Roadmap sync is MANDATORY** — when archiving an OpenSpec change (`/opsx-archive`), the agent MUST check and update `docs/features/feature-catalog.md` and `docs/features/roadmap.md` (local-only files, not in public repo) before performing the archive move. This includes: updating ✅ markers for completed features, updating statistics counts, updating ABC integration status, and checking off milestone items. If the user skips this step in the archive flow, the agent MUST still remind them after the archive completes.
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

All **code** artifacts — code, docstrings, comments, specs, design docs, proposals, tasks — **SHALL be written in English**.

We may converse in Chinese, but everything committed to the repository is English unless explicitly noted.

## Testing

- `tests/` mirrors `src/hecate/` structure (`test_engine/`, `test_models/`, `test_api/`, `test_services/`).
- Single `conftest.py` at `tests/`: `db_session` (AsyncSession + auto-rollback), `setup_database` (autouse, create_all/drop_all per test), `client` (httpx AsyncClient with DI overrides).
- **Do NOT create separate engines in test files** — use `db_session` from conftest.
- `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` decorator needed.
- Database: in-memory SQLite (`sqlite+aiosqlite://`). Never connect to real PostgreSQL in unit tests.
- Engine tests use lightweight stub classes (`SimpleWorker`, `InterruptWorker`) instead of mocking frameworks.
- No factories — create models inline with `db_session.add()` + `await db_session.flush()`.
- ruff S101 (assert in tests) is expected — per-file-ignores in pyproject.toml handle it.
- **Integration tests** (tests that need ChannelManager, PregelRuntime, GraphCompiler, LLMService integration) must wait until the actual integration code is implemented — do not write integration tests for features that are ABC-only.

## What to do / What not to do

- **Do** run `ruff check src/hecate/ tests/ && ruff format --check src/ tests/ && mypy src/ && python -m pytest tests/ -q` before committing.
- **Do** ensure **0 errors** locally before pushing to GitHub. If any check fails, fix it first.
- **Do** use `conftest.py`'s `db_session` fixture in all test files that need database access.
- **Do** add new Python packages to `pyproject.toml` dependencies immediately when installing locally. Never use a package in code without declaring it.
- **Do** write tests for new engine ABCs: test the ABC is not instantiable, test InMemory implementations, test edge cases. Do NOT write tests that reference integration points (ChannelManager, PregelRuntime, etc.) until those integration points actually exist.
- **Don't** renumber feature IDs — use letter suffixes.
- **Don't** commit PDF files or large binary assets.
- **Don't** add comments to code unless the logic is non-obvious.
- **Don't** use `as any`, `@ts-ignore` or equivalent type suppression.
- **Don't** import from `engine/` in `api/` — route through `services/` + `EnginePort`.
- **Don't** use `git commit --no-verify` to skip pre-commit hooks.
- **Don't** assume test failures are "pre-existing" without investigating.
