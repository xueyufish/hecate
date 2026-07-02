## Why

Hecate has a complete RAG pipeline (ingest → chunk → embed → hybrid search) and agent runtime (graph execution → LLM calls → tool invocation), but **no way to measure their quality**. Without evaluation, there is no feedback loop for improving retrieval accuracy, response faithfulness, or agent behavior. This is a prerequisite for P3's 40+ built-in evaluators, evaluation datasets, and report dashboards.

## What Changes

- **Evaluator framework**: Abstract base class `Evaluator` with async `evaluate()` interface, per-evaluator LLM configuration, and structured `Score` output
- **Evaluation dataset management**: CRUD for test datasets containing (query, expected_answer, context) tuples, stored in PostgreSQL
- **RAG evaluators (7.1)**: Context Precision, Context Recall, Faithfulness, Answer Relevancy — implemented via optional Ragas integration (`[rag]` dependency)
- **Agent evaluators (7.2)**: Correctness, Relevancy, Completeness — self-implemented using LLM-as-Judge pattern
- **Evaluation execution engine**: Run evaluation tasks against datasets, aggregate scores, produce `EvaluationRun` results
- **REST API**: `/api/evaluation/datasets`, `/api/evaluation/runs`, `/api/evaluation/scores` endpoints
- **Database models**: `EvaluationDatasetModel`, `EvaluationItemModel`, `EvaluationRunModel`, `EvaluationScoreModel` in SQLAlchemy async

## Capabilities

### New Capabilities
- `evaluation-framework`: Evaluator ABC, Score types, evaluation execution engine, per-evaluator LLM config
- `evaluation-dataset`: Dataset CRUD, item management, data import/export
- `rag-evaluation`: RAG-specific evaluators (context precision, context recall, faithfulness, answer relevancy) with optional Ragas backend
- `agent-evaluation`: Agent-specific evaluators (correctness, relevancy, completeness) using LLM-as-Judge
- `evaluation-api`: REST API endpoints for datasets, runs, and scores

### Modified Capabilities
<!-- No existing capability requirements are changing -->

## Impact

- **New code**: `services/evaluation/` (service, evaluator ABC, dataset manager, types), `models/evaluation.py` (4 new ORM models), `api/management/evaluation.py` (REST routes)
- **New dependency**: `ragas` as optional `[rag]` dependency in pyproject.toml
- **Database migration**: 4 new tables (evaluation_datasets, evaluation_items, evaluation_runs, evaluation_scores)
- **No breaking changes**: All new code, no existing APIs modified
