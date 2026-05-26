# AGENTS.md — Hecate

## What this repo is

Hecate is an **enterprise-grade, open-source, self-hosted, model-agnostic, MCP-first Agent platform** built with Python 3.12+, FastAPI, and SQLAlchemy 2.0 async. P1 implementation is complete (§1-§11, 88 tasks) — ready for P2 work.

## Commands

```bash
# Install (uses uv + venv at .venv/)
source .venv/bin/activate && uv pip install -e ".[dev]"

# Or use lock file for exact versions
uv pip sync uv.lock

# Run all tests
python -m pytest tests/ -v

# Run a single test file or function
python -m pytest tests/test_engine/test_pregel.py -v
python -m pytest tests/test_engine/test_pregel.py::test_linear_execution -v

# Lint → format → typecheck (run all before committing)
ruff check src/hecate/ tests/
ruff format src/hecate/ tests/
mypy src/

# Start infrastructure (PostgreSQL 16, Qdrant, MinIO)
docker compose -f docker/docker-compose.yml up -d

# Run database migrations (requires PostgreSQL running)
alembic upgrade head

# Run the application
uvicorn hecate.main:app --reload
```

## Key files (read these first on any new session)

| File | Purpose |
|------|---------|
| `openspec/changes/p1-execution-engine-core/tasks.md` | **P1 task list** — 88 tasks in 11 groups, all complete |
| `docs/features/feature-catalog.md` | 156 features across 14 domains, P1→P4 |
| `docs/research/reports/00-architecture-decisions.md` | 10 architecture decisions (AD-1~AD-10) |
| `docs/design/architecture.md` | Top-level architecture v0.2 |
| `schemas/graph-dsl.schema.json` | Graph DSL JSON Schema (4 node types, 4 channel types) |

## Architecture layers

```
engine/     → Zero external deps (no imports from services/, api/, models/); jsonschema is sole exception
services/   → Depends on models/, engine/ports (abstract interfaces only), and external libraries
api/        → Depends on services/ and models/; never imports engine/ directly
models/     → Pure data definitions (ORM + Pydantic); no business logic
core/       → Infrastructure: config (pydantic-settings), database (async SQLAlchemy), DI
```

- Engine `__init__.py` is empty — import directly from submodules (e.g., `from hecate.engine.pregel import PregelRuntime`).
- All I/O MUST be async. Engine communicates with services through `EnginePort` ABC only.
- `get_db()` in `core/database.py` is the FastAPI dependency — auto-commits on success, auto-rolls back on error.

## Implementation status

**Done (§1-§11)**: All P1 tasks completed:
- §1: Project skeleton (pyproject.toml, Docker, .env)
- §2: Data models (9 ORM + Pydantic schemas + Alembic)
- §3: Graph DSL + Compiler
- §4: Execution engine (Channel, Checkpoint, Pregel, interrupt, subgraph, ports)
- §5: API layer (FastAPI app, DI, CRUD, OpenAI compat, SSE streaming, rate limiting)
- §6: LLM routing (LiteLLM, streaming, tool calling, fallback)
- §7: RAG pipeline (embedding, parser, chunker, indexer, searcher, MinIO)
- §8: Security layer (LLM Guard, PII anonymization, NeMo Guardrails)
- §9: MCP integration (client, tool sync, tool calling)
- §10: End-to-end integration (conversation service, tests)
- §11: Documentation (README, AGENTS.md)

**Next (P2)**: Frontend canvas, multi-agent orchestration, Temporal integration.

## Gotchas and non-obvious facts

- **Python env**: uv + Python 3.12, venv at `.venv/`. Use `uv pip install`, not bare `pip install`.
- **Git**: development on `f_dev` branch.
- **CheckpointModel** inherits `Base` (not `BaseModel`) — intentionally immutable, no `updated_at`/`deleted_at`.
- **AgentModel.model_config_db** — ORM column named `model_config` via `mapped_column("model_config", JSON)` to avoid Pydantic's `model_config` collision. CreateSchema uses `alias="model_config"`, ReadSchema uses `serialization_alias="model_config"`.
- **metadata_ alias** — 5 models use `metadata_` (Python) → `metadata` (SQL) to avoid SQLAlchemy's reserved `metadata` attribute. ReadSchema uses `Field(validation_alias="metadata_")`.
- **graph_dsl.py** loads JSON Schema from disk on every `parse_graph()` call — not cached. Path uses `Path(__file__).parent.parent.parent.parent / "schemas"` which is fragile in installed packages.
- **compiler._detect_unreachable()** uses BFS from entry point; logs WARNING for unreachable nodes (does not raise).
- **engine/command.py** is a re-export of `Command` from `types.py` — currently unused (dead code).
- **PERSISTENT_TOPIC** has identical behavior to TOPIC — persistence semantic not yet implemented (P2).
- **StreamMode.MESSAGES / DEBUG** defined but not yielded in PregelRuntime (P2).
- **conftest.py `client` fixture** imports `from hecate.main import app` — now works since `main.py` exists.
- **`_resolve_next_nodes_after_interrupt()`** hardcodes `edge.target.get("true")` for dict targets — assumes conditional edges always follow interrupts.

## Conventions

### Project workflow

- Feature IDs: `X.Y.Z` pattern (e.g., `1.3.1`, `9.4a`). Append letter suffixes — never renumber.
- OpenSpec changes: proposal → design → specs → tasks. Mark tasks complete in `tasks.md` immediately.
- Research notes: overview → architecture → key findings → conclusion.
- Reports numbered `00`–`05`. `00` is the master decision summary.
- Update `research-tracker.md` when research items change status.
- Maintain P1→P4 priority ordering and update counts when features change.
- Run `ruff check` + `ruff format` + `mypy` before committing.

### Coding rules (enforced by ruff E/F/I/N/W/UP/B/S/SIM)

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

## Testing

- `tests/` mirrors `src/hecate/` structure (`test_engine/`, `test_models/`, `test_api/`, `test_services/`).
- Single `conftest.py` at root: `db_session` (AsyncSession + auto-rollback), `setup_database` (autouse, create_all/drop_all per test).
- **Do NOT create separate engines in test files** — use `db_session` from conftest.
- `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` decorator needed.
- Database: in-memory SQLite (`sqlite+aiosqlite://`). Never connect to real PostgreSQL in unit tests.
- Engine tests use lightweight stub classes (`SimpleWorker`, `InterruptWorker`) instead of mocking frameworks.
- No factories — create models inline with `db_session.add()` + `await db_session.flush()`.
- ruff S101 (assert in tests) is expected — only run `ruff check src/hecate/`, not tests.

## What to do / What not to do

- **Do** run `ruff check src/hecate/ && ruff format --check src/hecate/ tests/ && python -m pytest tests/ -q` before committing.
- **Do** use `conftest.py`'s `db_session` fixture in all test files.
- **Don't** renumber feature IDs — use letter suffixes.
- **Don't** commit PDF files or large binary assets.
- **Don't** add comments to code unless the logic is non-obvious.
- **Don't** use `as any`, `@ts-ignore` or equivalent type suppression.
- **Don't** import from `engine/` in `api/` — route through `services/` + `EnginePort`.
