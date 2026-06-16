# Contributing to Hecate

Thank you for your interest in contributing to Hecate! This document covers everything you need to get started.

## Development Setup

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- PostgreSQL 16 (or SQLite for unit tests)
- Qdrant (for vector search features)

### Install

```bash
git clone https://github.com/xueyufish/hecate.git
cd hecate

uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

### Verify Your Setup

Run all four checks before submitting a PR:

```bash
ruff check src/hecate/ tests/
ruff format --check src/ tests/
mypy src/
python -m pytest tests/ -q
```

These same checks run as pre-commit hooks and in CI.

## Project Structure

```
src/hecate/
├── engine/       # Execution engine (zero external deps, except jsonschema)
├── services/     # Capability services (LLM, RAG, memory, tools, security)
├── api/          # FastAPI routes (OpenAI-compatible + management API)
├── models/       # SQLAlchemy ORM + Pydantic schemas
└── core/         # Infrastructure (config, database, DI, rate limiting)
```

**Key rule**: The engine layer must never import from `services/`, `api/`, or `models/`. Services communicate with the engine through the abstract `EnginePort` interface.

## Coding Conventions

- `from __future__ import annotations` at the top of every Python file
- All public functions and methods require type annotations
- Mutable defaults: use `None`, never `[]` or `{}`
- `except:` without exception type is prohibited — always use `except X as e`
- `assert` is prohibited in production code (use in tests only)
- f-strings only; no `+`/`+=` for string concatenation in loops
- No commented-out code — delete it
- Docstrings on all modules, public classes, and public methods
- Inline comments: explain **why**, not **what**

## Testing

- Tests live in `tests/`, mirroring `src/hecate/` structure
- Single `conftest.py` at `tests/conftest.py` — provides `db_session`, `setup_database`, and `client` fixtures
- Database: in-memory SQLite (`sqlite+aiosqlite://`). Never connect to real PostgreSQL in unit tests
- `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` needed
- Engine tests use lightweight stub classes, not mocking frameworks

## OpenSpec Workflow

All changes follow the OpenSpec workflow:

1. **Propose** — Create a change proposal with `/opsx-propose`
2. **Design** — Write design docs and spec deltas
3. **Tasks** — Break down into implementable tasks
4. **Implement** — Work through tasks with `/opsx-apply`
5. **Verify** — Run all checks (ruff, mypy, pytest)
6. **Archive** — Close with `/opsx-archive`

See `openspec/specs/` for existing feature specifications and `openspec/changes/archive/` for completed changes.

## Pull Request Process

1. Create a feature branch from `f_dev`
2. Make your changes following the conventions above
3. Ensure all four checks pass locally
4. Open a PR targeting `f_dev`
5. Describe what changed and why
6. Link any relevant issues

## Questions?

- Open a [GitHub Issue](https://github.com/xueyufish/hecate/issues) for bugs or feature requests
- Read the [Architecture](docs/design/architecture.md) document for system overview
- Check [Engine Design](docs/design/engine-design.md) for execution engine details
