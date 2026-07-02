# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Hecate is an enterprise-grade, open-source, self-hosted, model-agnostic, MCP-first Agent platform built with Python 3.12+, FastAPI, and SQLAlchemy 2.0 async. Currently in P1 implementation phase — the execution engine core is complete, API/service layers are under development.

## Commands

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Install with optional capability groups
pip install -e ".[llm,rag,security]"

# Run all tests
pytest

# Run a single test file
pytest tests/test_engine/test_pregel.py

# Run a specific test function
pytest tests/test_engine/test_pregel.py::test_linear_execution

# Lint and format
ruff check src/ tests/
ruff format src/ tests/

# Type check
mypy src/

# Start infrastructure (PostgreSQL, Qdrant, MinIO)
docker compose -f docker/docker-compose.yml up -d

# Run database migrations
alembic upgrade head

# Run the application
uvicorn hecate.main:app --reload
```

## Key Reference Files

| File | Purpose |
|------|---------|
| `docs/features/feature-catalog.md` | Authoritative feature list — 156 features across 14 capability domains, prioritized P1→P4 |
| `docs/research/research-tracker.md` | Single source of truth for research progress — 5 phases, 80 research items |
| `docs/research/reports/00-architecture-decisions.md` | 10 core architecture decisions (AD-1~AD-10) + tech stack overview |
| `docs/design/architecture.md` | Top-level architecture v0.2 |
| `openspec/changes/p1-execution-engine-core/` | P1 OpenSpec change — proposal, design, specs, tasks |

## Architecture

Five-layer architecture with strict dependency rules:

```
engine/     → Zero external deps (no imports from services/, api/, models/); jsonschema is sole exception
services/   → Depends on models/, engine/ports (abstract interfaces only), external libraries
api/        → Depends on services/ and models/; never imports engine/ directly
models/     → Pure data definitions (ORM + Pydantic); no business logic
core/       → Infrastructure: config (pydantic-settings), database (async SQLAlchemy), DI
```

### Execution Engine (src/hecate/engine/)

The engine uses a **Pregel/BSP-inspired** execution model:

- **PregelRuntime** — executes compiled graphs in superstep cycles with checkpointing and interrupt/resume
- **ChannelManager** — manages typed state channels (`LAST_VALUE`, `TOPIC`, `PERSISTENT_TOPIC`, `ACCUMULATOR`)
- **graph_dsl.py + compiler.py** — parse and validate graph JSON DSL into `CompiledGraph`
- **worker.py** — abstract `Worker` base class; nodes execute by reading channel snapshots and returning `WorkerResult`
- **command.py** — `Command` dataclass for control flow (`goto`, `return`, `interrupt`, `update`)
- **ports.py** — `EnginePort` ABC defines the boundary interface between engine and external services (LLM, tools, knowledge, checkpoints, conversation)
- **templates.py** — built-in graph templates (e.g., three-layer agent: Guard → Planner → Sub-Agent)
- **subgraph.py** — subgraph composition support

Node types: `CONVERSATION`, `TOOL_CALL`, `CONDITION`, `AGENT`.

### Data Models (src/hecate/models/)

All ORM models extend `BaseModel` (UUID PK, timestamps, soft delete). Key tables: `AgentModel`, `SessionModel`, `CheckpointModel`, `ConversationModel`, `MessageModel`, `DocumentModel`, `KnowledgeModel`, `SkillModel`, `ToolModel`.

Pydantic schemas follow naming: `XxxCreateSchema` / `XxxUpdateSchema` / `XxxReadSchema`.

### Configuration & Database

- `core/config.py` — `Settings` class loads from `.env` via pydantic-settings
- `core/database.py` — async engine + session factory; `get_db()` is the FastAPI dependency
- Tests use in-memory SQLite (`sqlite+aiosqlite://`); never connect to real PostgreSQL in unit tests

### Architecture Decisions (ADR-001~020)

20 ADRs in `docs/design/adr/`: graph-first orchestration, five-layer architecture, checkpoint persistence, skill system, progressive worker pool, four-level memory, unified graph templates, security via hooks, dual API design, React Flow canvas, A2A protocol, MCP Streamable HTTP, Agentic RL, ontology action system, OAG, Platform SPI architecture, knowledge graph architecture, Zero Trust identity, visual workflow node types, async execution + distributed state. See `docs/research/reports/00-architecture-decisions.md` for the original research-phase analysis (AD-1~AD-10, now formalized as ADR-001~010).

## Coding Conventions

- Every file starts with `from __future__ import annotations`
- All public functions/methods require type hints
- All I/O is async; CPU-bound work uses `ProcessPoolExecutor`
- Concurrency: `asyncio.Queue` for coroutine coordination, `asyncio.Lock` for shared mutable state
- DI via FastAPI `Depends()` in `core/deps.py`; never import sessions directly
- `assert` is prohibited in production code; use proper validation
- No commented-out code — delete unused code entirely
- Docstrings required on **all** modules, public classes, and public methods — private (`_` prefix) methods are exempt only when truly self-explanatory
- Inline comments only for non-obvious logic — explain **why**, not **what**
- Docstrings must explain: purpose, parameters (when not obvious from name/type), return value/shape, and important behavior or side effects
- Module-level docstrings must explain the module's role in the architecture
- f-strings for formatting; no `+`/`+=` string concatenation in loops

### Security Coding

- All external input must be validated before use
- Use SQLAlchemy ORM only; raw SQL string concatenation is prohibited
- API keys and passwords must come from environment variables, never hardcoded
- Sensitive data (passwords, tokens, PII) must not appear in logs
- Error responses must not expose internal stack traces or implementation details
- Set `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` on API responses
- Configure explicit CORS origin whitelist; avoid `Access-Control-Allow-Origin: *`
- All public endpoints must have rate limiting
- All LLM input/output must pass through LLM Guard scanners

### Naming

| Category | Convention | Example |
|----------|-----------|---------|
| Classes | PascalCase | `PregelRuntime` |
| Functions/methods | snake_case | `execute_graph()` |
| Constants | UPPER_SNAKE | `DEFAULT_TIMEOUT` |
| SQLAlchemy models | XxxModel | `AgentModel` |
| Pydantic schemas | XxxCreateSchema | `AgentCreateSchema` |

## Testing

- Tests mirror `src/hecate/` under `tests/` (e.g., `test_engine/`, `test_models/`)
- `conftest.py` provides `db_session` and `client` fixtures; database is recreated per test
- `pytest-asyncio` with `asyncio_mode = "auto"` — no need for explicit `@pytest.mark.asyncio`
- External services (LLM, Qdrant, MinIO) must be mocked in tests

## Documentation Conventions

- Feature IDs use `X.Y.Z` pattern (e.g., `1.3.1`, `9.4a`). New features appended to existing IDs use letter suffixes — never renumber existing IDs
- Research notes follow: overview → architecture → key findings → conclusion
- Reports are numbered `00`–`05`; `00` is the master decision summary
- OpenSpec changes follow: proposal → design → specs → tasks
- Update `research-tracker.md` whenever research items change status
- Maintain P1→P4 priority ordering and update counts when features change
- Mark tasks complete in `tasks.md` immediately after implementing each task
- Do not commit PDF files or large binary assets
