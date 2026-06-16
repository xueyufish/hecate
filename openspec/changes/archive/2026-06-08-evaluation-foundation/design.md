## Context

Hecate has a complete RAG pipeline (`services/rag/` — parser, chunker, embedding, hybrid search with RRF score fusion) and agent runtime (`engine/pregel.py` — graph execution with LLM/Tool/Condition workers). The RAG service already exposes `search_with_score_breakdown()` returning `HybridSearchResult` with `score`, `dense_score`, `sparse_score` fields. However, there is **no evaluation infrastructure** — no way to measure retrieval quality, response faithfulness, or agent behavior over time.

The research notes (`docs/research/notes/eval-security-prompt-comparison.md`, `docs/research/notes/langfuse.md`) document three reference platforms: Ragas (RAG metrics), LangFuse (Score data model + LLM-as-Judge), and DSPy (prompt optimization). Ragas is the strongest fit for 7.1 RAG Evaluation — it provides battle-tested metrics (faithfulness, context precision/recall, answer relevancy) that would take weeks to implement from scratch.

Current architecture constraints:
- `models/` layer: SQLAlchemy ORM + Pydantic schemas, async-first
- `services/` layer: business logic, depends on `models/` and `engine/ports`
- `api/` layer: FastAPI routers, depends on `services/`
- `engine/` layer: zero external deps — evaluation stays in `services/`
- Database: async SQLAlchemy with Alembic migrations
- All public methods require type annotations, `from __future__ import annotations`

## Goals / Non-Goals

**Goals:**
- Establish the Evaluator ABC framework that P3's 40+ evaluators will extend
- Implement 4 RAG evaluators (context precision, context recall, faithfulness, answer relevancy)
- Implement 3 Agent evaluators (correctness, relevancy, completeness)
- Provide evaluation dataset CRUD with PostgreSQL persistence
- Provide REST API for dataset management and evaluation execution
- Support per-evaluator LLM configuration (each evaluator can use a different model)

**Non-Goals:**
- P3 evaluation features (40+ evaluators, AI-synthesized datasets, online/offline tasks, dashboards, human annotation)
- Workflow-level evaluation (7.3)
- Prompt optimization (7.6a, 7.6b)
- Frontend UI for evaluation management
- Real-time online evaluation / sampling
- CI/CD integration

## Decisions

### D1: Evaluator ABC vs Ragas-only

**Decision**: Self-built `Evaluator` ABC framework with Ragas as optional backend.

**Rationale**: P3 requires 40+ evaluators — many will be custom (domain-specific, enterprise policy checks). Ragas only covers RAG metrics. A self-built ABC with pluggable backends gives us:
- Consistent interface across all evaluators (RAG, Agent, custom)
- Ragas as an optional `[rag]` dependency — users without Ragas installed still get Agent evaluators
- Easy to add new evaluators without modifying framework code

**Alternatives considered**:
- Ragas-only: Would not scale to Agent evaluators or custom enterprise metrics
- Full self-built: Would require implementing and validating faithfulness/context precision from scratch — weeks of work for no clear benefit

### D2: Ragas as optional dependency

**Decision**: `ragas` declared in `[rag]` optional dependency group in pyproject.toml.

**Rationale**: Ragas is a heavy dependency (pulls in LangChain, multiple LLM client libraries). Making it optional:
- Keeps base install lightweight
- RAG evaluators raise `ImportError` with helpful message if `ragas` not installed
- Users who only need Agent evaluation don't need Ragas
- Follows existing pattern: `[llm]`, `[rag]`, `[temporal]`, `[security]`, `[dev]` groups already exist

### D3: Per-evaluator LLM configuration

**Decision**: Each `Evaluator` instance accepts an `llm_config` parameter specifying model, temperature, and API base.

**Rationale**:
- Evaluation LLM should be different from production LLM to avoid bias
- Some evaluators need stronger models (faithfulness), others can use cheaper ones (format check)
- Consistent with Hecate's multi-model architecture (LiteLLM routing)
- Default: use the agent's configured model as fallback

### D4: Data model — 4 tables

**Decision**: Four new SQLAlchemy models:

| Model | Purpose |
|-------|---------|
| `EvaluationDatasetModel` | Named dataset with metadata |
| `EvaluationItemModel` | Individual test case (query, expected_answer, context) |
| `EvaluationRunModel` | Execution of evaluators against a dataset |
| `EvaluationScoreModel` | Individual score from one evaluator on one item |

**Rationale**: Follows LangFuse's Score data model pattern (dataset → run → scores). Normalized design allows:
- Reusing datasets across multiple runs
- Tracking score history over time
- Per-item score drill-down
- Future support for online evaluation (score traces directly)

### D5: Evaluation execution — synchronous batch

**Decision**: Evaluation runs execute synchronously (batch) — not streaming, not async background.

**Rationale**: For P2, evaluation is an offline activity — users run it explicitly and wait for results. P3 adds online evaluation (7.2c) and scheduled tasks (13.9) which will introduce async/background execution. Keeping P2 simple avoids premature complexity.

### D6: API design

**Decision**: Three resource groups under `/api/evaluation/`:

| Endpoint | Purpose |
|----------|---------|
| `/api/evaluation/datasets` | Dataset CRUD |
| `/api/evaluation/datasets/{id}/items` | Item management within a dataset |
| `/api/evaluation/runs` | Create/list/get evaluation runs |
| `/api/evaluation/runs/{id}/scores` | Scores for a specific run |

Follows existing Hecate API patterns (FastAPI router, Pydantic schemas, async endpoints).

## Risks / Trade-offs

| Risk | Impact | Mitigation |
|------|--------|------------|
| Ragas API instability (v0.x) | RAG evaluators break on upgrade | Pin Ragas version; wrap Ragas calls in adapter layer; self-built ABC isolates framework from Ragas internals |
| LLM-as-Judge cost | Each evaluation run calls LLM per item per evaluator | Per-evaluator LLM config allows using cheaper models; dataset size is user-controlled |
| LLM-as-Judge latency | Large datasets take minutes to evaluate | Document expected latency; P3 adds async/background execution |
| Evaluation quality depends on judge LLM | Weak judge LLM produces unreliable scores | Default to strong model (GPT-4o); document that evaluation quality is proportional to judge LLM capability |
| Database migration (4 new tables) | Standard migration risk | Follow existing Alembic patterns; no schema changes to existing tables |
