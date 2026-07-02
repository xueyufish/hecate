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

## Git Workflow

We use GitHub Flow. All changes must go through a feature branch and pull request — never commit directly to `main`.

### Branch Naming

| Prefix | Use case | Example |
|--------|----------|---------|
| `feat/` | New feature | `feat/context-engine-phase1` |
| `fix/` | Bug fix | `fix/audit-timedelta` |
| `docs/` | Documentation only | `docs/readme-badge` |
| `chore/` | Tooling, CI, config | `chore/commit-convention` |
| `refactor/` | Code restructure | `refactor/worker-abc` |

### Workflow

```bash
# 1. Create feature branch from latest main
git checkout main && git pull
git checkout -b feat/your-feature

# 2. Make changes, commit (pre-commit hooks run automatically)
git add -A && git commit -m "feat(scope): description"

# 3. Push and create PR
git push -u origin feat/your-feature
# → Create PR on GitHub, target: main

# 4. After merge, clean up
git checkout main && git pull
git branch -d feat/your-feature
```

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

## Commit Message Convention

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

### Types

| Type | Purpose |
|------|---------|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `style` | Formatting/style (no logic change) |
| `refactor` | Code refactor (no feature/fix) |
| `perf` | Performance improvement |
| `test` | Add/update tests |
| `build` | Build system or dependencies |
| `ci` | CI configuration changes |
| `chore` | Maintenance/misc |
| `revert` | Revert a previous commit |

### Rules

- Use present tense, imperative mood: "add" not "added"
- Keep description under 72 characters
- One logical change per commit
- Reference issues in footer: `Closes #123`, `Refs #456`
- For breaking changes, add `!` after type: `feat!: remove deprecated endpoint`
- Never commit secrets (`.env`, credentials, private keys)
- Never use `--no-verify` to skip hooks

### Scopes

Use the module name as scope when applicable:

```
feat(engine): add ContextEngine integration to PregelRuntime
fix(services): handle None values in LLMService.invoke
docs(api): update OpenAI-compatible endpoint examples
test(models): add tenant isolation edge case tests
ci: add PR title lint workflow
```

### Enforcement

Commit messages are validated in two places:

1. **Local** — `commitizen` pre-commit hook (run `pre-commit install --hook-type commit-msg` after cloning)
2. **CI** — GitHub Action validates PR titles using `amannn/action-semantic-pull-request`

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

1. Fork the repository and create a feature branch from `main`
2. Make your changes following the conventions above
3. Ensure all four checks pass locally
4. Open a PR targeting `main`
5. Describe what changed and why
6. Link any relevant issues

## Questions?

- Open a [GitHub Issue](https://github.com/xueyufish/hecate/issues) for bugs or feature requests
- Read the [Architecture](docs/design/architecture.md) document for system overview
- Check [Engine Design](docs/design/engine-design.md) for execution engine details
