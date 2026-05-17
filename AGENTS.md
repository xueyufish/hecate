# AGENTS.md — Hecate

## What this repo is

Hecate is an **enterprise-grade, open-source, self-hosted, model-agnostic, MCP-first Agent platform**. The repo contains design documentation and the P1 implementation (execution engine core).

## Key files (read these first on any new session)

| File | Purpose |
|------|---------|
| `docs/features/feature-catalog.md` | **Authoritative feature list** — 156 features across 14 capability domains, prioritized P1→P4 |
| `docs/research/research-tracker.md` | **Single source of truth for research progress** — 5 phases, 80 research items |
| `docs/research/reports/00-architecture-decisions.md` | 10 core architecture decisions (AD-1~AD-10) + tech stack overview |
| `docs/design/architecture.md` | Top-level architecture v0.2, 4 chapters + ADR section |
| `openspec/changes/p1-execution-engine-core/` | **P1 OpenSpec change** — proposal, design, specs, tasks (56 tasks, 11 groups) |

## Directory structure

```
hecate/
├── src/hecate/
│   ├── main.py                    # FastAPI app entry
│   ├── api/                       # API layer (v1/ OpenAI compat + management/)
│   ├── engine/                    # Execution engine core (~5900 LOC)
│   ├── models/                    # ORM models + Pydantic schemas (9 tables)
│   ├── services/                  # Capability services (llm/, rag/, security/, mcp/)
│   └── core/                      # Config, database, dependency injection
├── tests/                         # Mirror src/hecate/ structure
├── docs/                          # Design documentation
│   ├── features/                  # Feature catalog
│   ├── research/                  # Research notes + reports
│   ├── design/                    # Architecture docs
│   └── refs/                      # Reference materials
├── openspec/changes/              # OpenSpec change artifacts
├── schemas/                       # JSON Schema definitions
├── docker/                        # Docker Compose config
├── alembic/                       # Database migrations
└── pyproject.toml                 # Project config + dependencies
```

## Conventions

### Project conventions

- Feature IDs use the pattern `X.Y.Z` (e.g., `1.3.1`, `9.4a`). New features appended to existing IDs use letter suffixes.
- Research notes follow: overview → architecture → key findings → conclusion.
- Reports are numbered `00`–`05`. `00` is the master decision summary.
- OpenSpec changes follow spec-driven schema: proposal → design → specs → tasks.

### Critical context

- **AD-1 to AD-10 all decided**: Self-built engine, five-layer architecture, Checkpoint+memory cache, SKILL.md+multi-source, thread pool→process pool, BGE-M3 RAG, unified graph templates, LLM Guard+NeMo Guardrails, OpenAI compat + Hecate management API, React Flow (P2).
- **Tech stack**: Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2.0 async, PostgreSQL 16, Qdrant, MinIO, LiteLLM, BGE-M3, Docling, LangFuse, MCP, Docker Compose.
- **Product positioning**: Open-source, self-hosted, model-agnostic, MCP-first. "拒绝供应商锁定".
- **OpenSpec change**: `openspec/changes/p1-execution-engine-core/` (4/4 artifacts complete, implementation in progress).

### What to do vs. what not to do

- **Do** update `research-tracker.md` whenever research items change status.
- **Do** maintain the P1→P4 priority ordering and update counts when features change.
- **Do** run `ruff check` + `ruff format` + `mypy` before committing code changes.
- **Do** mark tasks complete in `tasks.md` immediately after implementing each task.
- **Don't** renumber existing feature IDs — use letter suffixes for additions.
- **Don't** commit PDF files or large binary assets.
- **Don't** add comments to code unless the logic is non-obvious.

## Coding Conventions

### Python style

| Rule | Description |
|------|-------------|
| PEP 8 | All Python code MUST comply with [PEP 8](https://peps.python.org/pep-0008/); enforced by ruff (E, W, I, N rules) |
| Type hints | All public functions and methods MUST have type annotations |
| Future annotations | All files MUST start with `from __future__ import annotations` |
| Mutable defaults | Function parameters with mutable defaults MUST use `None` instead of `[]` or `{}` |
| Bare except | `except:` without exception type is PROHIBITED; always specify the exception class |
| Raise with exception | All `raise` statements MUST include an exception instance |
| Except syntax | MUST use `except X as x` syntax |
| Finally restrictions | `return` and `break` MUST NOT appear in `finally` blocks |
| Assert | `assert` MUST NOT be used in production code; use proper validation |
| For-in iteration | Use `for x in iterable` instead of `for i in range(len(x))`; use `enumerate()` when index needed |
| Resource management | Use `with` statement for files, database sessions, and other resources |
| Path handling | Use `os.path` or `pathlib` instead of string concatenation for file paths |
| Generators | Prefer generator comprehensions over list comprehensions for large data |
| No commented-out code | Delete unused code completely; do not comment it out |
| Exception for errors | Use exceptions to indicate errors, not return `None` |
| String formatting | Use f-strings (Python 3.12+); avoid `+` or `+=` for string concatenation in loops |
| Docstrings | All public classes and methods MUST have docstrings in English; private methods (`_` prefix) and self-explanatory ones are exempt |

### Naming conventions

| Category | Convention | Example |
|----------|-----------|---------|
| Modules | `snake_case` | `graph_dsl.py`, `checkpoint.py` |
| Classes | `PascalCase` | `PregelRuntime`, `ChannelManager` |
| Functions/methods | `snake_case` | `execute_graph()`, `save_checkpoint()` |
| Constants | `UPPER_SNAKE` | `DEFAULT_TIMEOUT`, `MAX_WORKERS` |
| Private members | `_prefix` | `_internal_state`, `_validate()` |
| Pydantic models | `XxxCreateSchema` / `XxxUpdateSchema` / `XxxReadSchema` | `AgentCreateSchema` |
| SQLAlchemy models | `XxxModel` | `AgentModel`, `SessionModel` |

### Architecture layers

```
engine/     → Zero external dependencies (no imports from services/, api/, models/)
services/   → Depends only on models/ and external libraries
api/        → Depends on services/ and models/; never imports engine/ directly
models/     → Pure data definitions; no business logic
core/       → Infrastructure: config, database, dependency injection
```

- All I/O operations MUST be async (database, HTTP, file operations).
- Dependency injection through FastAPI `Depends()` in `core/deps.py`; never import session directly.
- Engine communicates with services through `EnginePort` interface only.

### Security coding

| Rule | Description |
|------|-------------|
| Input validation | All external input MUST be validated before use |
| SQL injection | Use SQLAlchemy ORM only; raw SQL string concatenation is PROHIBITED |
| Secrets management | API keys and passwords MUST come from environment variables, never hardcoded |
| Logging | Sensitive data (passwords, tokens, PII) MUST NOT appear in logs |
| Error responses | Error responses MUST NOT expose internal stack traces or implementation details |
| HTTP security headers | Set `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` on API responses |
| CORS | Configure explicit origin whitelist; avoid `Access-Control-Allow-Origin: *` |
| Rate limiting | All public endpoints MUST have rate limiting |
| LLM I/O scanning | All LLM input/output MUST pass through LLM Guard scanners |

### Concurrency

- Prefer `async`/`await` for all I/O-bound work.
- Use `asyncio.Queue` for coordinating coroutines.
- Use `concurrent.futures.ProcessPoolExecutor` for CPU-bound parallel computation.
- Protect shared mutable state with `asyncio.Lock`.

### Testing

| Convention | Description |
|-----------|-------------|
| Directory | `tests/` mirrors `src/hecate/` structure (`test_engine/`, `test_api/`, `test_services/`) |
| Naming | Test files named `test_<module>.py`; test functions named `test_<scenario>` |
| Fixtures | Common fixtures in `conftest.py` (`db_session`, `client`) |
| Mocking | External services (LLM, Qdrant, MinIO) MUST be mocked in tests |
| Async | Use `@pytest.mark.asyncio` for all async test functions |
| Database | Tests use in-memory SQLite; never connect to real PostgreSQL in unit tests |

### Frontend (future, TypeScript)

- Use `const` by default, `let` when mutation needed; `var` is PROHIBITED.
- Use `camelCase` for functions and variables; `PascalCase` for components and classes.
- `eval()` is PROHIBITED with untrusted data.
- Do not modify built-in prototypes.
- Details to be expanded when frontend implementation begins.
