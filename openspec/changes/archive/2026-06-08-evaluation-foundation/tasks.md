## Tasks

### Task 1: Evaluation types and Evaluator ABC
- [x] Create `src/hecate/services/evaluation/__init__.py` with module docstring
- [x] Create `src/hecate/services/evaluation/types.py` with `Score` (metric_name, value 0.0–1.0, reasoning, source), `EvalInput` (query, retrieved_contexts, generated_answer, expected_answer, tool_calls, metadata), `EvalOutput` (scores, metadata, duration_ms), `LLMConfig` (model, temperature, api_base), `EvaluationRunResult` (run_id, dataset_id, scores per item, per-metric averages, total_duration_ms)
- [x] Create `src/hecate/services/evaluation/evaluator.py` with abstract `Evaluator` class (name, description properties; abstract async `evaluate(input: EvalInput) -> EvalOutput`)
- [x] Add `value` range validation (0.0–1.0) in `Score.__post_init__`
- [x] Add `source` enum validation ("llm_judge", "deterministic", "human")

**Files**: `src/hecate/services/evaluation/types.py`, `src/hecate/services/evaluation/evaluator.py`
**Spec**: evaluation-framework

### Task 2: Database models and Alembic migration
- [x] Create `src/hecate/models/evaluation.py` with 4 SQLAlchemy models:
  - `EvaluationDatasetModel` (id UUID, name, description, metadata_ JSON, created_at, updated_at, items relationship)
  - `EvaluationItemModel` (id UUID, dataset_id FK, query TEXT NOT NULL, expected_answer TEXT nullable, context JSON nullable, metadata_ JSON nullable, created_at, updated_at)
  - `EvaluationRunModel` (id UUID, dataset_id FK, evaluator_configs JSON, status ENUM pending/running/completed/failed, started_at, completed_at, created_at)
  - `EvaluationScoreModel` (id UUID, run_id FK, item_id FK, metric_name VARCHAR, value FLOAT, reasoning TEXT nullable, source VARCHAR, created_at)
- [x] Create Pydantic schemas: `EvaluationDatasetCreateSchema`, `EvaluationDatasetUpdateSchema`, `EvaluationDatasetReadSchema`, `EvaluationItemCreateSchema`, `EvaluationItemReadSchema`, `EvaluationRunCreateSchema`, `EvaluationRunReadSchema`, `EvaluationScoreReadSchema`
- [x] Generate Alembic migration: `alembic revision --autogenerate -m "add evaluation tables"`
- [x] Verify migration applies cleanly: `alembic upgrade head`

**Files**: `src/hecate/models/evaluation.py`, `alembic/versions/`
**Spec**: evaluation-dataset

### Task 3: Dataset service
- [x] Create `src/hecate/services/evaluation/dataset_service.py` with `EvaluationDatasetService` class
- [x] Implement async `create_dataset(name, description, metadata) -> EvaluationDatasetReadSchema`
- [x] Implement async `get_dataset(dataset_id) -> EvaluationDatasetReadSchema`
- [x] Implement async `list_datasets(page, page_size) -> PaginatedResult`
- [x] Implement async `update_dataset(dataset_id, name, description, metadata) -> EvaluationDatasetReadSchema`
- [x] Implement async `delete_dataset(dataset_id) -> None` (cascade delete items)
- [x] Implement async `add_items(dataset_id, items: list[EvaluationItemCreateSchema]) -> int`
- [x] Implement async `list_items(dataset_id, page, page_size) -> PaginatedResult`
- [x] Implement async `delete_item(dataset_id, item_id) -> None`
- [x] Implement async `import_json(dataset_id, json_data: list[dict]) -> ImportStats`
- [x] Implement async `export_json(dataset_id) -> list[dict]`
- [x] Validate query is non-empty on add_items

**Files**: `src/hecate/services/evaluation/dataset_service.py`
**Spec**: evaluation-dataset

### Task 4: RAG evaluators (Ragas-backed)
- [x] Add `ragas` to `[rag]` optional dependencies in `pyproject.toml` with pinned version
- [x] Create `src/hecate/services/evaluation/rag_evaluators.py`
- [x] Implement `ContextPrecisionEvaluator(Evaluator)` — uses Ragas `ContextPrecision` metric, raises `ImportError` if ragas not installed
- [x] Implement `ContextRecallEvaluator(Evaluator)` — uses Ragas `ContextRecall` metric
- [x] Implement `FaithfulnessEvaluator(Evaluator)` — uses Ragas `Faithfulness` metric
- [x] Implement `AnswerRelevancyEvaluator(Evaluator)` — uses Ragas `AnswerRelevancy` metric
- [x] Each evaluator accepts `llm_config: LLMConfig | None` and uses it to configure Ragas's LLM
- [x] Wrap all Ragas calls in try/except to convert Ragas errors to Hecate evaluation errors

**Files**: `src/hecate/services/evaluation/rag_evaluators.py`, `pyproject.toml`
**Spec**: rag-evaluation

### Task 5: Agent evaluators (LLM-as-Judge)
- [x] Create `src/hecate/services/evaluation/agent_evaluators.py`
- [x] Implement `CorrectnessEvaluator(Evaluator)` — LLM-as-Judge comparing generated vs expected answer, returns Score(metric_name="correctness", value=-1.0) when no expected_answer
- [x] Implement `RelevancyEvaluator(Evaluator)` — LLM-as-Judge assessing response relevance to query
- [x] Implement `CompletenessEvaluator(Evaluator)` — LLM-as-Judge assessing coverage of query aspects
- [x] Each evaluator uses `LLMConfig` to configure the judge LLM, falls back to default model
- [x] Define LLM-as-Judge prompt templates as module-level constants in `services/evaluation/prompts.py`

**Files**: `src/hecate/services/evaluation/agent_evaluators.py`, `src/hecate/services/evaluation/prompts.py`
**Spec**: agent-evaluation

### Task 6: Evaluation engine
- [x] Create `src/hecate/services/evaluation/engine.py` with `EvaluationEngine` class
- [x] Implement async `run(evaluators: list[Evaluator], dataset: EvaluationDatasetModel) -> EvaluationRunResult`
- [x] Execute each evaluator against each dataset item in nested loops
- [x] Catch exceptions per-evaluator-per-item — log error, record failed Score with reasoning="Evaluator error: {message}"
- [x] Compute per-metric averages across all items
- [x] Track total execution duration in milliseconds
- [x] Create `EvaluationRunModel` and `EvaluationScoreModel` records in database

**Files**: `src/hecate/services/evaluation/engine.py`
**Spec**: evaluation-framework

### Task 7: REST API endpoints
- [x] Create `src/hecate/api/evaluation.py` with FastAPI router (prefix="/api/evaluation", tags=["evaluation"])
- [x] Implement `POST /datasets` — create dataset, return 201
- [x] Implement `GET /datasets` — list datasets with pagination
- [x] Implement `GET /datasets/{dataset_id}` — get single dataset
- [x] Implement `PUT /datasets/{dataset_id}` — update dataset
- [x] Implement `DELETE /datasets/{dataset_id}` — delete dataset with cascade, return 204
- [x] Implement `POST /datasets/{dataset_id}/items` — add items, return 201 with count
- [x] Implement `GET /datasets/{dataset_id}/items` — list items with pagination
- [x] Implement `DELETE /datasets/{dataset_id}/items/{item_id}` — delete item, return 204
- [x] Implement `POST /runs` — create and execute evaluation run (dataset_id + evaluator names), return 201 with EvaluationRunResult
- [x] Implement `GET /runs` — list runs, optional filter by dataset_id
- [x] Implement `GET /runs/{run_id}` — get run with summary stats
- [x] Implement `GET /runs/{run_id}/scores` — get all scores for a run
- [x] Register router in `src/hecate/main.py`

**Files**: `src/hecate/api/evaluation.py`, `src/hecate/main.py`
**Spec**: evaluation-api

### Task 8: Tests
- [x] Create `tests/test_services/test_evaluation/test_types.py` — Score validation (range, source enum), EvalInput/Output construction
- [x] Create `tests/test_services/test_evaluation/test_evaluator_abc.py` — Evaluator is not instantiable, subclass with evaluate() works
- [x] Create `tests/test_services/test_evaluation/test_dataset_service.py` — CRUD, add_items validation, import/export, pagination (uses db_session)
- [x] Create `tests/test_services/test_evaluation/test_engine.py` — batch execution, error isolation, score aggregation (uses mock evaluators)
- [x] Create `tests/test_api/test_evaluation_api.py` — API endpoint tests (uses client fixture)
- [x] Agent evaluator tests use mock LLM responses (no real API calls)
- [x] RAG evaluator tests skip if ragas not installed (use `pytest.importorskip("ragas")`)

**Files**: `tests/test_services/test_evaluation/`, `tests/test_api/test_evaluation_api.py`
**Spec**: all

### Task 9: Verification
- [x] Run `ruff check src/hecate/services/evaluation/ src/hecate/models/evaluation.py src/hecate/api/evaluation.py tests/test_services/test_evaluation/ tests/test_api/test_evaluation_api.py`
- [x] Run `ruff format --check src/ tests/`
- [x] Run `mypy src/hecate/services/evaluation/ src/hecate/models/evaluation.py src/hecate/api/evaluation.py`
- [x] Run `python -m pytest tests/test_services/test_evaluation/ tests/test_api/test_evaluation_api.py -v`
- [x] Run full suite: `python -m pytest tests/ -q`
